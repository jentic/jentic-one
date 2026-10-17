"""``GET /catalog?q=`` with a pluggable ranking strategy, against a real DB.

A fake strategy, registered for the test only, returns deliberately messy
output (out of order, non-finite scores, an entry outside the filtered set,
duplicates) so these tests pin what ``CatalogService.list_all`` guarantees on
top of any strategy: validity, ``(-score, api_id)`` order, keyset paging that
never skips or repeats, the walk's strategy recorded in the cursor, and the
failure policy (page-1 fallback to ``token`` for the whole walk; 503 on a
later page, including when the cursor's strategy is configured but not
registered in this process; 400 for a cursor naming a strategy that is not the
configured one).
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
from collections.abc import AsyncGenerator, Iterator, Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from structlog.testing import capture_logs

from jentic_one.registry.services.catalog import manifest_builder as mb
from jentic_one.registry.services.catalog import search_metrics
from jentic_one.registry.services.catalog.search import (
    CatalogMatch,
    CatalogSearchRequest,
    RankedEntry,
    registered_catalog_search_strategy,
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


class _HostileInt(int):
    """A score type whose negation (used by the sort) raises."""

    def __neg__(self) -> int:
        raise RuntimeError("hostile score")


class _FakeStrategy:
    name = "fake"

    def __init__(self) -> None:
        self.fail = False
        self.all_invalid = False
        self.hostile = False
        self.delay_s: float | None = None
        self.calls = 0
        self.last_entries: list[str] = []
        self.last_request: CatalogSearchRequest | None = None

    async def rank(self, request: CatalogSearchRequest) -> list[RankedEntry]:
        self.calls += 1
        self.last_entries = sorted(e.api_id for e in request.entries)
        self.last_request = request
        if self.fail:
            raise RuntimeError("strategy exploded")
        if self.delay_s is not None:
            await asyncio.sleep(self.delay_s)
        if self.all_invalid:
            return [RankedEntry(e, math.nan) for e in request.entries]
        if self.hostile:
            return [RankedEntry(e, _HostileInt(1)) for e in request.entries]
        rows: list[RankedEntry] = []
        for e in request.entries:  # input order, i.e. not ranked
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
    strategy = _FakeStrategy()
    with registered_catalog_search_strategy(strategy):
        monkeypatch.setattr(web_context.config.catalog, "search_strategy", strategy.name)
        yield strategy


@pytest.fixture()
def failure_metrics(monkeypatch: pytest.MonkeyPatch) -> InMemoryMetricReader:
    """Route the strategy-failure counter to an in-memory reader for this test."""
    reader = InMemoryMetricReader()
    counter = (
        MeterProvider(metric_readers=[reader])
        .get_meter("test")
        .create_counter("jentic_one.catalog_search.strategy_failures")
    )
    monkeypatch.setattr(search_metrics, "_failure_counter", counter)
    return reader


def _failures(reader: InMemoryMetricReader) -> list[tuple[dict[str, Any], int]]:
    data = reader.get_metrics_data()
    if data is None:
        return []
    return [
        (dict(point.attributes or {}), int(point.value))  # type: ignore[union-attr]
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
        for point in metric.data.data_points
    ]


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
    assert fake.last_request is not None
    assert fake.last_request.q == _Q
    assert fake.last_request.deadline_ms == web_context.config.catalog.search_deadline_ms
    assert dict(fake.last_request.options) == {}
    assert fake.last_request.manifest_revision is not None


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
def test_cursor_with_malformed_strategy_name_is_400(admin_client: TestClient) -> None:
    cursor = encode_catalog_cursor("pay.com", 1.0, strategy="not_registered" + "x" * 200)
    resp = admin_client.get("/catalog", params={"q": _Q, "cursor": cursor})
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "Invalid pagination cursor"
    assert "not_registered" not in resp.text


@pytest.mark.usefixtures("catalog")
def test_over_long_cursor_is_400_like_any_invalid_cursor(admin_client: TestClient) -> None:
    """The 1 KB cap is the cursor parser's, so clients see one restartable 400."""
    resp = admin_client.get("/catalog", params={"q": _Q, "cursor": "A" * 2000})
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "Invalid pagination cursor"


@pytest.mark.usefixtures("catalog")
def test_cursor_naming_a_strategy_that_is_not_configured_is_400(
    admin_client: TestClient, fake: _FakeStrategy, failure_metrics: InMemoryMetricReader
) -> None:
    """A registered but unselected strategy can't be chosen by forging ``st``."""
    cursor = encode_catalog_cursor("pay.com", 1.0, strategy="other")
    resp = admin_client.get("/catalog", params={"q": _Q, "cursor": cursor})

    assert resp.status_code == 400, resp.text
    assert "restart the search without a cursor" in resp.json()["detail"]
    assert "other" not in resp.json()["detail"]
    assert fake.calls == 0
    assert _failures(failure_metrics) == [
        ({"strategy": "unknown", "reason": "not_configured", "outcome": "rejected"}, 1)
    ]


@pytest.mark.usefixtures("catalog")
def test_switching_strategy_mid_walk_rejects_the_old_cursor(
    admin_client: TestClient,
    web_context: Context,
    fake: _FakeStrategy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deselecting a strategy turns its in-flight walks into restart-the-search 400s."""
    first = admin_client.get("/catalog", params={"q": _Q, "limit": 2}).json()
    assert _cursor_json(first["next_cursor"])["st"] == "fake"  # type: ignore[index]
    monkeypatch.setattr(web_context.config.catalog, "search_strategy", "token")

    resp = admin_client.get(
        "/catalog", params={"q": _Q, "limit": 2, "cursor": first["next_cursor"]}
    )

    assert resp.status_code == 400, resp.text
    assert fake.calls == 1


@pytest.mark.usefixtures("catalog")
def test_token_cursor_keeps_working_after_a_strategy_is_selected(
    admin_client: TestClient, web_context: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A walk started under ``token`` (no ``st``) finishes on ``token`` whatever is configured."""
    first = admin_client.get("/catalog", params={"q": _Q, "limit": 2}).json()
    strategy = _FakeStrategy()
    with registered_catalog_search_strategy(strategy):
        monkeypatch.setattr(web_context.config.catalog, "search_strategy", strategy.name)
        rest = _walk_from(admin_client, {"q": _Q, "limit": 2}, first["next_cursor"])
    monkeypatch.setattr(web_context.config.catalog, "search_strategy", "token")

    assert [first, *rest] == _walk(admin_client, {"q": _Q, "limit": 2})
    assert strategy.calls == 0


@pytest.mark.usefixtures("catalog")
def test_configured_strategy_unregistered_after_page_one_is_503(
    admin_client: TestClient,
    web_context: Context,
    monkeypatch: pytest.MonkeyPatch,
    failure_metrics: InMemoryMetricReader,
) -> None:
    """Defensive: a cursor for the configured strategy, which this process lacks, is a 503.

    App assembly refuses that config, so this pins the safety net, not a
    rollout path.
    """
    strategy = _FakeStrategy()
    with registered_catalog_search_strategy(strategy):
        monkeypatch.setattr(web_context.config.catalog, "search_strategy", strategy.name)
        first = admin_client.get("/catalog", params={"q": _Q, "limit": 2}).json()
    assert _cursor_json(first["next_cursor"])["st"] == "fake"  # type: ignore[index]

    resp = admin_client.get(
        "/catalog", params={"q": _Q, "limit": 2, "cursor": first["next_cursor"]}
    )

    assert resp.status_code == 503, resp.text
    assert resp.headers["Retry-After"] == "1"
    assert _failures(failure_metrics) == [
        ({"strategy": "fake", "reason": "unregistered", "outcome": "unavailable"}, 1)
    ]


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
    failure_metrics: InMemoryMetricReader,
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
    assert _failures(failure_metrics) == [
        ({"strategy": "fake", "reason": "error", "outcome": "fallback"}, 1)
    ]

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
def test_all_invalid_ranking_falls_back_on_page_one(
    admin_client: TestClient,
    web_context: Context,
    fake: _FakeStrategy,
    monkeypatch: pytest.MonkeyPatch,
    failure_metrics: InMemoryMetricReader,
) -> None:
    """Rows that all normalise away are a strategy failure, not an empty result."""
    fake.all_invalid = True
    with capture_logs() as logs:
        pages = _walk(admin_client, {"q": _Q, "limit": 2})

    assert all("st" not in (_cursor_json(p["next_cursor"]) or {}) for p in pages)
    monkeypatch.setattr(web_context.config.catalog, "search_strategy", "token")
    assert pages == _walk(admin_client, {"q": _Q, "limit": 2})
    assert [e["reason"] for e in logs if e["event"] == "catalog_search_strategy_fallback"] == [
        "invalid"
    ]
    [dropped] = [e for e in logs if e["event"] == "catalog_search_rows_dropped"]
    assert dropped["log_level"] == "warning"
    assert _failures(failure_metrics) == [
        ({"strategy": "fake", "reason": "invalid", "outcome": "fallback"}, 1)
    ]


@pytest.mark.usefixtures("catalog")
def test_hostile_score_types_fall_back_on_page_one(
    admin_client: TestClient,
    web_context: Context,
    fake: _FakeStrategy,
    monkeypatch: pytest.MonkeyPatch,
    failure_metrics: InMemoryMetricReader,
) -> None:
    """An ``int`` subclass whose ``__neg__`` raises is an invalid row, never a 500."""
    fake.hostile = True
    pages = _walk(admin_client, {"q": _Q, "limit": 2})

    assert all("st" not in (_cursor_json(p["next_cursor"]) or {}) for p in pages)
    assert _failures(failure_metrics) == [
        ({"strategy": "fake", "reason": "invalid", "outcome": "fallback"}, 1)
    ]
    monkeypatch.setattr(web_context.config.catalog, "search_strategy", "token")
    assert pages == _walk(admin_client, {"q": _Q, "limit": 2})


@pytest.mark.usefixtures("catalog")
def test_strategy_failure_after_page_one_is_503(
    admin_client: TestClient, fake: _FakeStrategy, failure_metrics: InMemoryMetricReader
) -> None:
    first = admin_client.get("/catalog", params={"q": _Q, "limit": 2}).json()
    assert _cursor_json(first["next_cursor"])["st"] == "fake"  # type: ignore[index]
    fake.fail = True

    with capture_logs() as logs:
        resp = admin_client.get(
            "/catalog", params={"q": _Q, "limit": 2, "cursor": first["next_cursor"]}
        )

    assert resp.status_code == 503, resp.text
    assert resp.headers["Retry-After"] == "1"
    body = resp.json()
    assert body["type"].endswith("catalog_search_unavailable")
    assert body["detail"] == (
        "Catalog search is temporarily unavailable; retry or restart the search without a cursor"
    )
    assert "fake" not in resp.text
    assert "strategy exploded" not in resp.text
    # The strategy's exception is not chained into the logged traceback.
    assert "strategy exploded" not in json.dumps(logs, default=str)
    assert _failures(failure_metrics) == [
        ({"strategy": "fake", "reason": "error", "outcome": "unavailable"}, 1)
    ]

    fake.fail = False  # the same cursor resumes once the strategy recovers
    resp = admin_client.get(
        "/catalog", params={"q": _Q, "limit": 2, "cursor": first["next_cursor"]}
    )
    assert resp.status_code == 200
    assert resp.json()["data"][0]["api_id"] == _expected(_MANIFEST)[2]


@pytest.mark.usefixtures("catalog")
def test_partially_dropped_rows_log_at_debug(admin_client: TestClient, fake: _FakeStrategy) -> None:
    """Dropped rows on a page that still has valid ones are not a warning."""
    with capture_logs() as logs:
        resp = admin_client.get("/catalog", params={"q": _Q, "limit": 2})

    assert resp.status_code == 200, resp.text
    [dropped] = [e for e in logs if e["event"] == "catalog_search_rows_dropped"]
    assert dropped["log_level"] == "debug"
    assert dropped["invalid"] > 0 and dropped["foreign"] == 1 and dropped["duplicate"] > 0


@pytest.mark.usefixtures("catalog")
def test_invalid_ranking_after_page_one_is_503_without_retry_after(
    admin_client: TestClient, fake: _FakeStrategy, failure_metrics: InMemoryMetricReader
) -> None:
    """An all-invalid ranking repeats for the same request, so no retry hint."""
    first = admin_client.get("/catalog", params={"q": _Q, "limit": 2}).json()
    fake.all_invalid = True

    resp = admin_client.get(
        "/catalog", params={"q": _Q, "limit": 2, "cursor": first["next_cursor"]}
    )

    assert resp.status_code == 503, resp.text
    assert "Retry-After" not in resp.headers
    assert resp.json()["type"].endswith("catalog_search_unavailable")
    assert _failures(failure_metrics) == [
        ({"strategy": "fake", "reason": "invalid", "outcome": "unavailable"}, 1)
    ]


@pytest.mark.usefixtures("catalog")
def test_strategy_timeout_after_page_one_is_503(
    admin_client: TestClient,
    web_context: Context,
    fake: _FakeStrategy,
    monkeypatch: pytest.MonkeyPatch,
    failure_metrics: InMemoryMetricReader,
) -> None:
    first = admin_client.get("/catalog", params={"q": _Q, "limit": 2}).json()
    monkeypatch.setattr(web_context.config.catalog, "search_deadline_ms", 20)
    fake.delay_s = 2.0

    resp = admin_client.get(
        "/catalog", params={"q": _Q, "limit": 2, "cursor": first["next_cursor"]}
    )

    assert resp.status_code == 503, resp.text
    assert _failures(failure_metrics) == [
        ({"strategy": "fake", "reason": "timeout", "outcome": "unavailable"}, 1)
    ]
