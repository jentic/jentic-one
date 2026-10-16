"""Web-layer tests for POST /search — the api-filter contract the CLI depends on (#1080).

The service-level behavior is covered in
``tests/integration/registry/ingest/test_search_api_filter.py``; these tests pin
the router seam: a resolvable slash-slug filter is a 200, an unresolvable one is
a 422 problem detail with ``type: invalid_api_filter`` and a format hint, and a
partial ``revision_pins`` key gets the pins-specific hint. A strategy that fails
at query time maps to the 501 ``search_unsupported`` problem (#965).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.repos.search import SearchUnsupportedError, register_strategy
from jentic_one.registry.repos.search.protocol import SearchCursor, SearchHit
from jentic_one.registry.repos.search.registry import _STRATEGIES
from jentic_one.shared.context import Context

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _cleanup(web_context: Context) -> AsyncGenerator[None]:
    yield
    async with web_context.registry_db.session() as session:
        await session.execute(text("DELETE FROM registry.apis"))
        await session.commit()


@pytest.fixture()
async def seeded_api(web_context: Context) -> Api:
    """One imported API row — enough for filter resolution (hits need operations)."""
    async with web_context.registry_db.session() as session:
        api = Api(vendor="acme-example-com", name="pets", version="v1", display_name="Acme Pets")
        session.add(api)
        await session.commit()
        return api


async def test_search_with_slash_slug_filter_returns_200(
    authed_client: TestClient, seeded_api: Api
) -> None:
    """The canonical slug the CLI sends resolves at the router (was a 422, #1080)."""
    resp = authed_client.post(
        "/search",
        json={"query": "anything", "apis": ["acme-example-com/pets/v1"]},
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == []


async def test_search_unknown_api_filter_returns_422_with_hint(
    authed_client: TestClient, seeded_api: Api
) -> None:
    """An unresolvable filter maps to the typed 422 the CLI keys its error on."""
    resp = authed_client.post(
        "/search",
        json={"query": "anything", "apis": ["nope-example-com/nope/v9"]},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["type"] == "invalid_api_filter"
    assert "vendor[/name[/version]]" in body["detail"]


async def test_search_partial_revision_pin_key_returns_422_with_pins_hint(
    authed_client: TestClient, seeded_api: Api
) -> None:
    """Pin keys need the full triple; the 422 says so instead of the generic hint."""
    resp = authed_client.post(
        "/search",
        json={
            "query": "anything",
            "revision_pins": {"acme-example-com/pets": "00000000-0000-0000-0000-000000000000"},
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["type"] == "invalid_api_filter"
    assert "full 'vendor/name/version'" in body["detail"]


async def test_search_requires_auth(unauthed_client: TestClient) -> None:
    resp = unauthed_client.post("/search", json={"query": "anything"})
    assert resp.status_code == 401


class _QueryTimeUnsupportedStrategy:
    """Resolves fine, then fails inside ``search_operations`` (provider down)."""

    dialect = "postgres"
    name = "test-query-unsupported"

    async def search_operations(
        self,
        session: AsyncSession,
        *,
        query: str,
        api_filters: list[uuid.UUID] | None = None,
        revision_pins: dict[uuid.UUID, uuid.UUID] | None = None,
        limit: int = 20,
        cursor: SearchCursor | None = None,
    ) -> list[SearchHit]:
        raise SearchUnsupportedError("embedding provider unreachable")


@pytest.fixture()
def query_unsupported_mode(web_context: Context, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    key = (_QueryTimeUnsupportedStrategy.dialect, _QueryTimeUnsupportedStrategy.name)
    register_strategy(_QueryTimeUnsupportedStrategy)
    monkeypatch.setattr(web_context.config.search, "search_mode", key[1])
    yield
    _STRATEGIES.pop(key, None)


async def test_search_query_time_unsupported_returns_501(
    authed_client: TestClient, query_unsupported_mode: None
) -> None:
    """A strategy failing at query time is the typed 501, not a bare 500 (#965)."""
    resp = authed_client.post("/search", json={"query": "anything"})
    assert resp.status_code == 501
    body = resp.json()
    assert body["type"] == "search_unsupported"
    assert body["detail"] == "embedding provider unreachable"
