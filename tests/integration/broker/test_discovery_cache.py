"""Integration tests for the broker's cached route discovery (#1713).

Runs the production wiring (``InProcessRegistryResolver`` with a
``DiscoveryCache``) against a real Registry DB and asserts:

- a repeat lookup is served without a registry statement;
- promoting or archiving a revision in this process invalidates the cached
  route on commit, whatever the TTL;
- ``broker.discovery_cache_ttl_s = 0`` reads the registry on every call;
- a cached pinned route never bypasses the per-caller pin check: another
  user's draft stays forbidden to them after its owner's call cached it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import delete, event, select, update

from jentic_one.broker.core.exceptions import UnauthorizedRevisionPinError
from jentic_one.broker.services.discovery import discover, resolve_pin_for_api
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.url_index import build_index_entry
from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.registry.repos.operation_repo import OperationInput, OperationRepository
from jentic_one.registry.repos.url_index_repo import UrlIndexRepository
from jentic_one.registry.services.revision_service import RevisionService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.route_generation import route_generation
from jentic_one.shared.config import BrokerConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ApiRevisionState
from jentic_one.shared.schemas import APIReference
from jentic_one.wiring import InProcessRegistryResolver, build_discovery_cache

pytestmark = pytest.mark.integration

VENDOR = "cache.example.com"
NAME = "routes"
VERSION = "v1"
HOST = "api.cache.example.com"
PATH = "/v1/widgets"
URL = f"https://{HOST}{PATH}"
OWNER = "usr_owner"
OTHER = "usr_other"
OPERATOR = Identity(sub=OWNER, permissions=["apis:write", "credentials:write"])


@pytest.fixture()
async def clean_registry(registry_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with registry_db.session() as session:
            await session.execute(delete(OperationURLIndex))
            await session.execute(delete(Operation))
            await session.execute(update(Api).values(current_revision_id=None))
            await session.execute(delete(ApiRevision))
            await session.execute(delete(Api))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _seed_revision(
    registry_db: DatabaseSession,
    api_id: uuid.UUID,
    *,
    state: ApiRevisionState,
    make_current: bool,
    submitted_by: str = OWNER,
) -> uuid.UUID:
    """One revision whose single GET operation serves ``URL``."""
    async with registry_db.session() as session:
        revision = ApiRevision(
            api_id=api_id, state=state.value, source_type="url", submitted_by=submitted_by
        )
        session.add(revision)
        await session.flush()
        rev_id = revision.id
        if make_current:
            await ApiRepository.set_current_revision(session, api_id, rev_id)
        op_ids = await OperationRepository.bulk_create(
            session, rev_id, [OperationInput(path=PATH, method="GET")], created_by="usr_test"
        )
        await UrlIndexRepository.upsert_entry(
            session,
            revision_id=rev_id,
            operation_id=op_ids[0],
            method="GET",
            entry=build_index_entry(HOST, PATH, "https"),
            created_by="usr_test",
        )
        await session.commit()
    return rev_id


@pytest.fixture()
async def seeded(registry_db: DatabaseSession, clean_registry: None) -> dict[str, uuid.UUID]:
    """A live published revision and an owner's draft, both serving ``URL``."""
    async with registry_db.session() as session:
        api = Api(vendor=VENDOR, name=NAME, version=VERSION)
        session.add(api)
        await session.commit()
        api_id = api.id
    live = await _seed_revision(
        registry_db, api_id, state=ApiRevisionState.PUBLISHED, make_current=True
    )
    draft = await _seed_revision(
        registry_db, api_id, state=ApiRevisionState.DRAFT, make_current=False
    )
    return {"api_id": api_id, "live": live, "draft": draft}


def _cached_resolver(registry_db: DatabaseSession, **broker: Any) -> InProcessRegistryResolver:
    # A long TTL, so only an invalidation (never expiry) can refresh a route.
    config = BrokerConfig(**{"discovery_cache_ttl_s": 300.0, **broker})
    return InProcessRegistryResolver(registry_db, discovery_cache=build_discovery_cache(config))


@contextmanager
def _count_statements(db: DatabaseSession) -> Iterator[list[str]]:
    statements: list[str] = []

    def _record(_conn: Any, _cursor: Any, sql: str, *_rest: Any) -> None:
        statements.append(sql)

    sync_engine = db.engine.sync_engine
    event.listen(sync_engine, "before_cursor_execute", _record)
    try:
        yield statements
    finally:
        event.remove(sync_engine, "before_cursor_execute", _record)


async def _operation_of(registry_db: DatabaseSession, revision_id: uuid.UUID) -> str:
    async with registry_db.session() as session:
        stmt = select(Operation.id).where(Operation.revision_id == revision_id)
        return (await session.execute(stmt)).scalar_one()


async def test_repeat_lookup_is_served_without_registry_statements(
    registry_db: DatabaseSession, seeded: dict[str, uuid.UUID]
) -> None:
    resolver = _cached_resolver(registry_db)

    with _count_statements(registry_db) as cold:
        first = await discover(resolver, method="GET", url=URL)
    with _count_statements(registry_db) as warm:
        second = await discover(resolver, method="GET", url=f"{URL}?page=2")

    assert first is not None and first == second
    assert first.operation.id == await _operation_of(registry_db, seeded["live"])
    assert cold, "the first lookup must read the registry"
    assert warm == []


async def test_promote_invalidates_cached_route(
    integration_context: Context, registry_db: DatabaseSession, seeded: dict[str, uuid.UUID]
) -> None:
    resolver = _cached_resolver(registry_db)
    before = await discover(resolver, method="GET", url=URL)
    assert before is not None
    assert before.operation.id == await _operation_of(registry_db, seeded["live"])

    await RevisionService(integration_context).promote(
        VENDOR, NAME, VERSION, str(seeded["draft"]), identity=OPERATOR
    )

    after = await discover(resolver, method="GET", url=URL)
    assert after is not None
    assert after.operation.id == await _operation_of(registry_db, seeded["draft"])


async def test_archive_invalidates_cached_route(
    integration_context: Context, registry_db: DatabaseSession, clean_registry: None
) -> None:
    async with registry_db.session() as session:
        api = Api(vendor=VENDOR, name=NAME, version=VERSION)
        session.add(api)
        await session.commit()
        api_id = api.id
    # Only a draft or a catalog-imported revision may be archived.
    live = await _seed_revision(
        registry_db, api_id, state=ApiRevisionState.IMPORTED, make_current=True
    )
    resolver = _cached_resolver(registry_db)
    assert await discover(resolver, method="GET", url=URL) is not None

    await RevisionService(integration_context).archive(
        VENDOR, NAME, VERSION, str(live), identity=OPERATOR
    )

    assert await discover(resolver, method="GET", url=URL) is None


async def test_routing_write_bumps_generation_only_on_commit(
    registry_db: DatabaseSession, seeded: dict[str, uuid.UUID]
) -> None:
    """A rolled-back promote leaves cached routes valid; a committed one invalidates them."""
    start = route_generation()
    async with registry_db.session() as session:
        await ApiRepository.set_current_revision(session, seeded["api_id"], seeded["draft"])
        await session.rollback()
    assert route_generation() == start

    async with registry_db.session() as session:
        await ApiRepository.set_current_revision(session, seeded["api_id"], seeded["draft"])
        await session.commit()
    assert route_generation() == start + 1


async def test_ttl_zero_reads_registry_every_call(
    registry_db: DatabaseSession, seeded: dict[str, uuid.UUID]
) -> None:
    resolver = _cached_resolver(registry_db, discovery_cache_ttl_s=0)
    await discover(resolver, method="GET", url=URL)

    with _count_statements(registry_db) as statements:
        assert await discover(resolver, method="GET", url=URL) is not None

    assert statements


async def test_cached_pinned_route_never_bypasses_the_pin_check(
    registry_db: DatabaseSession, seeded: dict[str, uuid.UUID]
) -> None:
    """The owner's pinned call caches the draft's route; another user still gets 403."""
    resolver = _cached_resolver(registry_db)
    pins = {(VENDOR, NAME, VERSION): f"rev_{seeded['draft'].hex}"}
    api = APIReference(vendor=VENDOR, name=NAME, version=VERSION)

    revision_id = await resolve_pin_for_api(
        resolver, api=api, pins=pins, identity=Identity(sub=OWNER)
    )
    assert revision_id == seeded["draft"]
    assert await discover(resolver, method="GET", url=URL, revision_id=revision_id) is not None

    with pytest.raises(UnauthorizedRevisionPinError):
        await resolve_pin_for_api(resolver, api=api, pins=pins, identity=Identity(sub=OTHER))
    # An unpinned lookup of the same URL is a different key: it serves the live
    # revision, never the draft route the owner's pinned call cached.
    unpinned = await discover(resolver, method="GET", url=URL)
    assert unpinned is not None
    assert unpinned.operation.id == await _operation_of(registry_db, seeded["live"])
