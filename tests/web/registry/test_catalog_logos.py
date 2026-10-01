"""Web tests for catalog vendor logos (``GET /catalog/{api_id}/logo``, ``_links.logo``).

Runs against the real Postgres test DB; only upstream HTTP is mocked, by patching
``catalog.fetch.httpx.AsyncClient`` with a MockTransport. ``_UPSTREAM`` decides what
the fake logo host answers and records every logo request, so tests can assert the
registry serves from its cache instead of refetching.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncGenerator, Iterator
from dataclasses import dataclass, field
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from jentic_one.registry.core.schema.catalog_logos import CatalogLogo
from jentic_one.registry.core.schema.catalog_snapshots import CatalogSnapshot
from jentic_one.registry.services.catalog import manifest_builder as mb
from jentic_one.registry.services.catalog.service import CatalogService
from jentic_one.shared.context import Context
from jentic_one.shared.db.utils import utcnow

pytestmark = pytest.mark.integration

_MANIFEST_BASE = "https://raw.githubusercontent.com/jentic/jentic-public-apis/main/apis"
_MANIFEST_URL = f"{_MANIFEST_BASE}/openapi/apis.json"
_LOGO_URL = f"{_MANIFEST_BASE}/openapi/stripe.com/logo.png"
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
_SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


@dataclass
class _Upstream:
    """What the fake logo host answers, plus a log of the logo requests it saw."""

    status: int = 200
    body: bytes = _PNG
    etag: str | None = '"upstream-v1"'
    requests: list[httpx.Request] = field(default_factory=list)


_UPSTREAM = _Upstream()


def _manifest() -> dict[str, object]:
    return {
        "include": [
            {
                "name": "stripe.com:main@2024-01-01 - Stripe API",
                "url": f"{_MANIFEST_BASE}/openapi/stripe.com/main/2024-01-01/apis.json",
                "image": _LOGO_URL,
            },
            {"url": f"{_MANIFEST_BASE}/openapi/slack.com/main/1.0/apis.json"},
        ]
    }


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url == _MANIFEST_URL:
        return httpx.Response(200, content=json.dumps(_manifest()).encode())
    if url == _LOGO_URL:
        _UPSTREAM.requests.append(request)
        headers = {"etag": _UPSTREAM.etag} if _UPSTREAM.etag else {}
        if _UPSTREAM.etag and request.headers.get("if-none-match") == _UPSTREAM.etag:
            return httpx.Response(304, headers=headers)
        # Upstream claims image/png whatever the bytes are: the registry must sniff.
        return httpx.Response(
            _UPSTREAM.status,
            content=_UPSTREAM.body,
            headers={"content-type": "image/png", **headers},
        )
    return httpx.Response(404)


@pytest.fixture(autouse=True)
def _mock_upstream() -> Iterator[None]:
    global _UPSTREAM
    _UPSTREAM = _Upstream()
    real_client_cls = httpx.AsyncClient

    def _factory(*_args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return real_client_cls(transport=httpx.MockTransport(_handler), **kwargs)  # type: ignore[arg-type]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("jentic_one.registry.services.catalog.fetch.httpx.AsyncClient", _factory)
        yield


@pytest.fixture(autouse=True)
async def _clean(web_context: Context) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with web_context.registry_db.session() as session:
            await session.execute(delete(CatalogSnapshot))
            await session.execute(delete(CatalogLogo))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _logo_row(ctx: Context) -> CatalogLogo | None:
    async with ctx.registry_db.session() as session:
        result = await session.execute(
            select(CatalogLogo).where(CatalogLogo.source_url == _LOGO_URL)
        )
        return result.scalar_one_or_none()


async def _age_cache(ctx: Context, *, seconds: int) -> None:
    async with ctx.registry_db.session() as session:
        row = (
            await session.execute(select(CatalogLogo).where(CatalogLogo.source_url == _LOGO_URL))
        ).scalar_one()
        row.fetched_at = utcnow() - timedelta(seconds=seconds)
        await session.commit()


def test_logo_requires_auth(unauthed_client: TestClient) -> None:
    assert unauthed_client.get("/catalog/stripe.com/logo").status_code == 401


def test_links_logo_only_when_manifest_lists_one(admin_client: TestClient) -> None:
    admin_client.post("/catalog:refresh")
    by_id = {e["api_id"]: e for e in admin_client.get("/catalog").json()["data"]}
    assert by_id["stripe.com"]["_links"]["logo"].endswith("/catalog/stripe.com/logo")
    assert by_id["slack.com"]["_links"].get("logo") is None
    entry = admin_client.get("/catalog/stripe.com").json()
    assert entry["_links"]["logo"].endswith("/catalog/stripe.com/logo")
    # Listing never fetches logos; only the logo endpoint does.
    assert _UPSTREAM.requests == []


async def test_logo_fetched_once_then_served_from_cache(
    admin_client: TestClient, web_context: Context
) -> None:
    admin_client.post("/catalog:refresh")
    first = admin_client.get("/catalog/stripe.com/logo")
    assert first.status_code == 200
    assert first.content == _PNG
    assert first.headers["content-type"] == "image/png"
    assert first.headers["etag"] == f'"{hashlib.sha256(_PNG).hexdigest()}"'
    assert first.headers["x-content-type-options"] == "nosniff"
    assert first.headers["content-security-policy"].startswith("default-src 'none'")
    assert first.headers["cache-control"].startswith("private")

    second = admin_client.get("/catalog/stripe.com/logo")
    assert second.status_code == 200
    assert second.content == _PNG
    assert len(_UPSTREAM.requests) == 1

    row = await _logo_row(web_context)
    assert row is not None
    assert (row.status, row.content, row.upstream_etag) == ("ok", _PNG, '"upstream-v1"')


def test_logo_if_none_match_answers_304(admin_client: TestClient) -> None:
    admin_client.post("/catalog:refresh")
    etag = admin_client.get("/catalog/stripe.com/logo").headers["etag"]
    r = admin_client.get("/catalog/stripe.com/logo", headers={"If-None-Match": etag})
    assert r.status_code == 304
    assert r.content == b""
    assert r.headers["etag"] == etag


async def test_stale_logo_revalidates_with_upstream_etag(
    admin_client: TestClient, web_context: Context
) -> None:
    admin_client.post("/catalog:refresh")
    admin_client.get("/catalog/stripe.com/logo")
    await _age_cache(web_context, seconds=8 * 86400)

    r = admin_client.get("/catalog/stripe.com/logo")
    assert r.status_code == 200
    assert r.content == _PNG
    assert len(_UPSTREAM.requests) == 2
    assert _UPSTREAM.requests[1].headers["if-none-match"] == '"upstream-v1"'
    row = await _logo_row(web_context)
    assert row is not None
    assert row.status == "ok"
    assert (utcnow() - row.fetched_at).total_seconds() < 60


async def test_failed_refetch_keeps_serving_cached_logo(
    admin_client: TestClient, web_context: Context
) -> None:
    admin_client.post("/catalog:refresh")
    admin_client.get("/catalog/stripe.com/logo")
    await _age_cache(web_context, seconds=8 * 86400)
    _UPSTREAM.status, _UPSTREAM.etag = 500, None

    r = admin_client.get("/catalog/stripe.com/logo")
    assert r.status_code == 200
    assert r.content == _PNG
    row = await _logo_row(web_context)
    assert row is not None
    assert (row.status, row.content) == ("error", _PNG)


async def test_svg_logo_is_refused_and_cached_as_unsupported(
    admin_client: TestClient, web_context: Context
) -> None:
    admin_client.post("/catalog:refresh")
    _UPSTREAM.body = _SVG

    r = admin_client.get("/catalog/stripe.com/logo")
    assert r.status_code == 404
    assert "catalog_logo_not_found" in json.dumps(r.json())
    assert admin_client.get("/catalog/stripe.com/logo").status_code == 404
    assert len(_UPSTREAM.requests) == 1
    row = await _logo_row(web_context)
    assert row is not None
    assert (row.status, row.content) == ("unsupported", None)


def test_oversized_logo_is_refused(admin_client: TestClient) -> None:
    admin_client.post("/catalog:refresh")
    _UPSTREAM.body = _PNG + b"\x00" * (256 * 1024)
    assert admin_client.get("/catalog/stripe.com/logo").status_code == 404


async def test_unreachable_logo_without_cache_404s_and_is_negatively_cached(
    admin_client: TestClient, web_context: Context
) -> None:
    admin_client.post("/catalog:refresh")
    _UPSTREAM.status = 503
    assert admin_client.get("/catalog/stripe.com/logo").status_code == 404
    assert admin_client.get("/catalog/stripe.com/logo").status_code == 404
    assert len(_UPSTREAM.requests) == 1
    row = await _logo_row(web_context)
    assert row is not None
    assert row.status == "error"


def test_entry_without_logo_404s_without_fetching(admin_client: TestClient) -> None:
    admin_client.post("/catalog:refresh")
    r = admin_client.get("/catalog/slack.com/logo")
    assert r.status_code == 404
    assert "catalog_logo_not_found" in json.dumps(r.json())
    assert _UPSTREAM.requests == []


def test_unknown_entry_logo_404(admin_client: TestClient) -> None:
    admin_client.post("/catalog:refresh")
    r = admin_client.get("/catalog/does-not-exist.com/logo")
    assert r.status_code == 404
    assert "catalog_entry_not_found" in json.dumps(r.json())


def test_to_view_has_logo_requires_listed_logo_and_enabled_logos() -> None:
    entry = mb.ManifestEntry(
        api_id="stripe.com",
        vendor="stripe.com",
        path="stripe.com",
        spec_url="https://example.com/openapi.json",
        github_url="",
        logo_source_url=_LOGO_URL,
    )
    assert CatalogService._to_view(entry, set(), logos_enabled=True).has_logo is True
    assert CatalogService._to_view(entry, set(), logos_enabled=False).has_logo is False
    bare = mb.ManifestEntry.from_dict({**entry.to_dict(), "logo_source_url": None})
    assert CatalogService._to_view(bare, set(), logos_enabled=True).has_logo is False
