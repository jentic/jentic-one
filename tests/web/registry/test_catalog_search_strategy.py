"""``GET /catalog?q=`` with a pluggable ranking strategy, against a real DB.

A fake strategy, registered for the test only, returns deliberately messy
output (out of order, non-finite scores, an entry outside the filtered set,
duplicates) so these tests pin what ``CatalogService.list_all`` guarantees on
top of any strategy: validity, ``(-score, api_id)`` order, keyset paging that
never skips or repeats, the walk's strategy recorded in the cursor, and the
failure policy (page-1 fallback to ``token`` for the whole walk; 503 on a
later page; 400 for a cursor naming an unregistered strategy).
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
from collections.abc import AsyncGenerator, Iterator, Mapping, Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from jentic_one.registry.services.catalog import manifest_builder as mb
from jentic_one.registry.services.catalog import search
from jentic_one.registry.services.catalog.search import (
    CatalogMatch,
    RankedEntry,
    register_catalog_search_strategy,
)
from jentic_one.shared.context import Context
from jentic_one.shared.pagination import encode_catalog_cursor
from tests.web.registry.conftest import (
    delete_registered_apis,
    register_catalog_entries,
    seed_catalog_snapshot,
)

pytestmark = pytest.mark.integration

_MANIFEST = [
    "zapi.com",
    "pay.com",
    "unrelated.org",
    "payments-api.net",
    "pay-api.com",
    "stripe.com",
    "openpay.dev",
    "api.pay.io",
    "paypal.com",
    "apiary.io",
    "weather.gov",
]
_REGISTERED = ["pay.com", "pay-api.com", "api.pay.io", "openpay.dev", "zapi.com"]
#: The strategy only returns an invalid (infinite) score for this one, so it never appears.
_ONLY_INVALID = "zapi.com"
#: The strategy omits this one: a strategy may return a subset.
_OMITTED = "weather.gov"
_Q = "pay api"


def _score(api_id: str) -> float:
    """Deterministic float scores with ties and non-trivial reprs (thirds)."""
    return (sum(map(ord, api_id)) % 4) / 3


class _FakeStrategy:
    name = "fake"

    def __init__(self) -> None:
        self.fail = False
        self.delay_s: float | None = None
        self.calls = 0
        self.last_entries: list[str] = []
        self.last_deadline_ms: int | None = None

    async def rank(
        self,
        q: str,
        entries: Sequence[mb.ManifestEntry],
        *,
        deadline_ms: int | None = None,
        context: Mapping[str, object] | None = None,
    ) -> list[RankedEntry]:
        self.calls += 1
        self.last_entries = sorted(e.api_id for e in entries)
        self.last_deadline_ms = deadline_ms
        if self.fail:
            raise RuntimeError("strategy exploded")
        if self.delay_s is not None:
            await asyncio.sleep(self.delay_s)
        rows: list[RankedEntry] = []
        for e in entries:  # input order, i.e. not ranked
            if e.api_id == _OMITTED:
                continue
            if e.api_id == _ONLY_INVALID:
                rows.append(RankedEntry(e, math.inf))
                continue
            match = CatalogMatch(("fake",)) if "pay" in e.api_id else None
            rows.append(RankedEntry(e, _score(e.api_id), match))
            # A lower-scoring duplicate and a NaN duplicate: both must be dropped.
            rows.append(RankedEntry(e, _score(e.api_id) - 1))
            rows.append(RankedEntry(e, math.nan))
        rows.append(RankedEntry(mb.ManifestEntry("evil.example", "x", None, "x"), 99.0))
        return rows


def _expected(api_ids: Sequence[str]) -> list[str]:
    kept = [a for a in api_ids if a not in (_OMITTED, _ONLY_INVALID)]
    return sorted(kept, key=lambda a: (-_score(a), a))


@pytest.fixture()
async def catalog(web_context: Context, clean_catalog: None) -> AsyncGenerator[None, None]:
    await seed_catalog_snapshot(web_context, _MANIFEST)
    await register_catalog_entries(web_context, _REGISTERED)
    try:
        yield
    finally:
        await delete_registered_apis(web_context)


@pytest.fixture()
def fake(monkeypatch: pytest.MonkeyPatch, web_context: Context) -> Iterator[_FakeStrategy]:
    """Register the fake strategy (test-scoped) and select it in config."""
    monkeypatch.setattr(search, "_STRATEGIES", dict(search._STRATEGIES))
    strategy = _FakeStrategy()
    register_catalog_search_strategy(strategy)
    monkeypatch.setattr(web_context.config.catalog, "search_strategy", strategy.name)
    yield strategy


def _cursor_json(cursor: str | None) -> dict[str, Any] | None:
    return None if cursor is None else json.loads(base64.b64decode(cursor))


def _walk(
    client: TestClient, params: dict[str, Any], *, after_first: Any = None
) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(100):
        query = {**params, **({"cursor": cursor} if cursor else {})}
        resp = client.get("/catalog", params=query)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        pages.append(body)
        if after_first is not None and len(pages) == 1:
            after_first()
        if not body["has_more"]:
            return pages
        cursor = body["next_cursor"]
    raise AssertionError("pagination did not terminate")


def _ids(pages: list[dict[str, Any]]) -> list[str]:
    return [e["api_id"] for p in pages for e in p["data"]]


@pytest.mark.usefixtures("catalog")
@pytest.mark.parametrize("limit", [1, 2, 3, 4, 50])
def test_strategy_walk_is_sorted_validated_and_never_skips_or_repeats(
    admin_client: TestClient, web_context: Context, fake: _FakeStrategy, limit: int
) -> None:
    pages = _walk(admin_client, {"q": _Q, "limit": limit})

    ids = _ids(pages)
    assert ids == _expected(_MANIFEST)
    assert len(ids) == len(set(ids))
    for page in pages[:-1]:
        cur = _cursor_json(page["next_cursor"])
        assert cur is not None
        assert cur["st"] == "fake"
        assert cur["s"] == _score(cur["id"])
    assert pages[-1]["next_cursor"] is None
    for item in (e for p in pages for e in p["data"]):
        if "pay" in item["api_id"]:
            assert item["match"] == {"channels": ["fake"]}
        else:
            assert "match" not in item
    assert fake.calls == len(pages)
    assert fake.last_deadline_ms == web_context.config.catalog.search_deadline_ms


@pytest.mark.usefixtures("catalog")
def test_filters_run_before_ranking(admin_client: TestClient, fake: _FakeStrategy) -> None:
    pages = _walk(admin_client, {"q": _Q, "registered_only": True, "limit": 2})

    assert fake.last_entries == sorted(_REGISTERED)
    assert _ids(pages) == _expected(_REGISTERED)


@pytest.mark.usefixtures("catalog")
def test_browse_and_token_free_queries_never_reach_the_strategy(
    admin_client: TestClient, fake: _FakeStrategy
) -> None:
    for params in ({"limit": 3}, {"q": "---", "limit": 3}):
        pages = _walk(admin_client, params)
        assert _ids(pages) == sorted(_MANIFEST)
        assert all("st" not in (_cursor_json(p["next_cursor"]) or {}) for p in pages)
    assert fake.calls == 0


@pytest.mark.usefixtures("catalog")
def test_cursor_naming_unregistered_strategy_is_400(admin_client: TestClient) -> None:
    cursor = encode_catalog_cursor("pay.com", 1.0, strategy="not_registered" + "x" * 200)
    with capture_logs() as logs:
        resp = admin_client.get("/catalog", params={"q": _Q, "cursor": cursor})
    assert resp.status_code == 400, resp.text
    assert "not_registered" not in resp.text
    [rejected] = [e for e in logs if e["event"] == "catalog_search_cursor_strategy_unregistered"]
    assert len(rejected["strategy"]) <= 32


@pytest.mark.usefixtures("catalog")
def test_cursor_strategy_wins_over_config(
    admin_client: TestClient,
    web_context: Context,
    fake: _FakeStrategy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A walk keeps its strategy even if the configured default changes mid-walk."""
    first = admin_client.get("/catalog", params={"q": _Q, "limit": 2}).json()
    monkeypatch.setattr(web_context.config.catalog, "search_strategy", "token")

    rest = _walk_from(admin_client, {"q": _Q, "limit": 2}, first["next_cursor"])

    assert _ids([first, *rest]) == _expected(_MANIFEST)
    assert fake.calls == 1 + len(rest)


def _walk_from(client: TestClient, params: dict[str, Any], cursor: str) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    while cursor:
        resp = client.get("/catalog", params={**params, "cursor": cursor})
        assert resp.status_code == 200, resp.text
        pages.append(resp.json())
        cursor = resp.json()["next_cursor"]
    return pages


@pytest.mark.usefixtures("catalog")
def test_page_one_failure_falls_back_to_token_for_the_whole_walk(
    admin_client: TestClient,
    web_context: Context,
    fake: _FakeStrategy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    params = {"q": _Q, "limit": 2}
    fake.fail = True

    def _recover() -> None:  # later pages must not switch back to the strategy
        fake.fail = False

    with capture_logs() as logs:
        pages = _walk(admin_client, params, after_first=_recover)

    assert fake.calls == 1
    assert all("st" not in (_cursor_json(p["next_cursor"]) or {}) for p in pages)
    assert all("match" not in e for p in pages for e in p["data"])
    fallback = [entry for entry in logs if entry["event"] == "catalog_search_strategy_fallback"]
    assert len(fallback) == 1
    assert fallback[0]["strategy"] == "fake"
    assert fallback[0]["reason"] == "error"
    assert fallback[0]["error_type"] == "RuntimeError"
    assert _Q not in json.dumps(fallback[0], default=str)
    assert "strategy exploded" not in json.dumps(fallback[0], default=str)

    monkeypatch.setattr(web_context.config.catalog, "search_strategy", "token")
    assert pages == _walk(admin_client, params)


@pytest.mark.usefixtures("catalog")
def test_page_one_timeout_falls_back_to_token(
    admin_client: TestClient,
    web_context: Context,
    fake: _FakeStrategy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(web_context.config.catalog, "search_deadline_ms", 20)
    fake.delay_s = 2.0

    with capture_logs() as logs:
        body = admin_client.get("/catalog", params={"q": _Q, "limit": 2}).json()

    assert "st" not in (_cursor_json(body["next_cursor"]) or {})
    assert [e["reason"] for e in logs if e["event"] == "catalog_search_strategy_fallback"] == [
        "timeout"
    ]


@pytest.mark.usefixtures("catalog")
def test_configured_but_unregistered_strategy_falls_back_on_page_one(
    admin_client: TestClient, web_context: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Startup refuses this config; if it slips through (re-config), search still works."""
    monkeypatch.setattr(web_context.config.catalog, "search_strategy", "gone")
    pages = _walk(admin_client, {"q": _Q, "limit": 2})
    assert _ids(pages)
    assert all("st" not in (_cursor_json(p["next_cursor"]) or {}) for p in pages)


@pytest.mark.usefixtures("catalog")
def test_strategy_failure_after_page_one_is_503(
    admin_client: TestClient, fake: _FakeStrategy
) -> None:
    first = admin_client.get("/catalog", params={"q": _Q, "limit": 2}).json()
    assert _cursor_json(first["next_cursor"])["st"] == "fake"  # type: ignore[index]
    fake.fail = True

    resp = admin_client.get(
        "/catalog", params={"q": _Q, "limit": 2, "cursor": first["next_cursor"]}
    )

    assert resp.status_code == 503, resp.text
    assert "catalog_search_unavailable" in resp.text
    assert "strategy exploded" not in resp.text

    fake.fail = False  # the same cursor resumes once the strategy recovers
    resp = admin_client.get(
        "/catalog", params={"q": _Q, "limit": 2, "cursor": first["next_cursor"]}
    )
    assert resp.status_code == 200
    assert resp.json()["data"][0]["api_id"] == _expected(_MANIFEST)[2]
