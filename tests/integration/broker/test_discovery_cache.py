"""Integration tests for the broker's cached route discovery (#1713).

Runs the production wiring (``InProcessRegistryResolver`` with a
``DiscoveryCache``) against a real Registry DB and asserts:

- a repeat lookup is served without a registry statement, and a miss reads
  the registry with no BEGIN/ROLLBACK (``read_session()``);
- in a deployed broker (its own process, so no registry commit ever reaches
  its routing generation) a promote or delete takes effect exactly when the
  default TTL lapses, and the previous route is served until then;
- when the registry write shares the broker's process, every routing write
  path (promote, archive, API delete, overlay rollback, import, URL-index
  rewrite) invalidates the cached route on commit, whatever the TTL;
- a SAVEPOINT neither bumps the generation on RELEASE nor discards an outer
  transaction's mark when it rolls back;
- ``broker.discovery_cache_ttl_s = 0`` reads the registry on every call;
- through the execute route, another user's pin never rides a route the
  owner's pinned call cached.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from sqlalchemy import delete, event, select, update
from starlette.requests import Request

from jentic_one.broker.core.discovery_cache import DiscoveryCache
from jentic_one.broker.core.exceptions import (
    OperationNotFoundError,
    UnauthorizedRevisionPinError,
)
from jentic_one.broker.services.discovery import discover
from jentic_one.broker.web.routers import execute as execute_mod
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.overlays import Overlay
from jentic_one.registry.core.schema.security_schemes import SecurityScheme, SecuritySchemeFlow
from jentic_one.registry.core.schema.servers import Server, ServerVariable
from jentic_one.registry.core.schema.spec_files import SpecFile
from jentic_one.registry.core.url_index import build_index_entry
from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.registry.repos.operation_repo import OperationInput, OperationRepository
from jentic_one.registry.repos.url_index_repo import UrlIndexRepository
from jentic_one.registry.services.api_service import ApiService
from jentic_one.registry.services.overlay_service import OverlayService
from jentic_one.registry.services.revision_service import RevisionService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.route_generation import route_generation
from jentic_one.shared.config import BrokerConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ORIGIN_CATALOG, ApiRevisionSourceType, ApiRevisionState
from jentic_one.shared.schemas import APIReference
from jentic_one.wiring import InProcessRegistryResolver, build_discovery_cache
from tests.harness.db_round_trips import trace_round_trips

pytestmark = pytest.mark.integration

VENDOR = "cache-example"
NAME = "routes"
VERSION = "v1"
HOST = "api.cache.example.com"
PATH = "/v1/widgets"
URL = f"https://{HOST}{PATH}"
DRAFT_ONLY_PATH = "/v1/gadgets"
DRAFT_ONLY_URL = f"https://{HOST}{DRAFT_ONLY_PATH}"
OWNER = "usr_owner"
OTHER = "usr_other"
OPERATOR = Identity(sub=OWNER, permissions=["apis:write", "credentials:write", "overlays:confirm"])


@pytest.fixture()
async def clean_registry(registry_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with registry_db.session() as session:
            await session.execute(delete(OperationURLIndex))
            await session.execute(delete(SecuritySchemeFlow))
            await session.execute(delete(SecurityScheme))
            await session.execute(delete(ServerVariable))
            await session.execute(delete(Server))
            await session.execute(delete(Operation))
            await session.execute(delete(SpecFile))
            await session.execute(delete(Overlay))
            await session.execute(update(Api).values(current_revision_id=None))
            await session.execute(delete(ApiRevision))
            await session.execute(delete(Api))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


async def _seed_api(registry_db: DatabaseSession) -> uuid.UUID:
    async with registry_db.session() as session:
        api = Api(vendor=VENDOR, name=NAME, version=VERSION)
        session.add(api)
        await session.commit()
        return api.id


async def _seed_revision(
    registry_db: DatabaseSession,
    api_id: uuid.UUID,
    *,
    state: ApiRevisionState,
    make_current: bool,
    submitted_by: str = OWNER,
    origin: str | None = None,
    path: str = PATH,
) -> uuid.UUID:
    """One revision whose single GET operation serves ``https://HOST{path}``."""
    async with registry_db.session() as session:
        revision = ApiRevision(
            api_id=api_id,
            state=state.value,
            source_type="url",
            submitted_by=submitted_by,
            origin=origin,
        )
        session.add(revision)
        await session.flush()
        rev_id = revision.id
        if make_current:
            await ApiRepository.set_current_revision(session, api_id, rev_id)
        op_ids = await OperationRepository.bulk_create(
            session, rev_id, [OperationInput(path=path, method="GET")], created_by="usr_test"
        )
        await UrlIndexRepository.upsert_entry(
            session,
            revision_id=rev_id,
            operation_id=op_ids[0],
            method="GET",
            entry=build_index_entry(HOST, path, "https"),
            created_by="usr_test",
        )
        await session.commit()
    return rev_id


@pytest.fixture()
async def seeded(registry_db: DatabaseSession, clean_registry: None) -> dict[str, uuid.UUID]:
    """A live published revision and an owner's draft, both serving ``URL``."""
    api_id = await _seed_api(registry_db)
    live = await _seed_revision(
        registry_db, api_id, state=ApiRevisionState.PUBLISHED, make_current=True
    )
    draft = await _seed_revision(
        registry_db, api_id, state=ApiRevisionState.DRAFT, make_current=False
    )
    return {"api_id": api_id, "live": live, "draft": draft}


def _cached_resolver(registry_db: DatabaseSession, **broker: Any) -> InProcessRegistryResolver:
    """The production wiring, in the writer's process, with a TTL no test outlives.

    Only an in-process invalidation (never expiry) can refresh one of its routes.
    """
    config = BrokerConfig(**{"discovery_cache_ttl_s": 300.0, **broker})
    return InProcessRegistryResolver(registry_db, discovery_cache=build_discovery_cache(config))


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _deployed_broker_resolver(
    registry_db: DatabaseSession, clock: _Clock
) -> InProcessRegistryResolver:
    """A resolver as a deployed broker runs it: default TTL, its own process.

    The broker never shares a process with a registry writer, so its routing
    generation never moves; the fixed generation models exactly that.
    """
    config = BrokerConfig()
    cache = DiscoveryCache(
        ttl_s=config.discovery_cache_ttl_s,
        negative_ttl_s=config.discovery_cache_negative_ttl_s,
        max_entries=config.discovery_cache_max_entries,
        generation=lambda: 0,
        clock=clock,
    )
    return InProcessRegistryResolver(registry_db, discovery_cache=cache)


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


async def _served_operation(resolver: InProcessRegistryResolver, url: str = URL) -> str | None:
    resolved = await discover(resolver, method="GET", url=url)
    return None if resolved is None else resolved.operation.id


# --- Cache hits -------------------------------------------------------------


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


async def test_cache_miss_reads_the_registry_in_a_read_session(
    registry_db: DatabaseSession, seeded: dict[str, uuid.UUID]
) -> None:
    """A miss loads through ``read_session()``, so it sends no BEGIN/ROLLBACK."""
    resolver = _cached_resolver(registry_db)

    with trace_round_trips(registry_db) as trace:
        assert await discover(resolver, method="GET", url=URL) is not None

    trace.assert_no_begin_or_rollback()


async def test_ttl_zero_reads_registry_every_call(
    registry_db: DatabaseSession, seeded: dict[str, uuid.UUID]
) -> None:
    resolver = _cached_resolver(registry_db, discovery_cache_ttl_s=0)
    await discover(resolver, method="GET", url=URL)

    with _count_statements(registry_db) as statements:
        assert await discover(resolver, method="GET", url=URL) is not None

    assert statements


# --- Deployed broker: the TTL is the only freshness bound -------------------


async def test_deployed_broker_serves_the_previous_route_until_the_ttl_lapses(
    integration_context: Context, registry_db: DatabaseSession, seeded: dict[str, uuid.UUID]
) -> None:
    clock = _Clock()
    resolver = _deployed_broker_resolver(registry_db, clock)
    live_op = await _operation_of(registry_db, seeded["live"])
    draft_op = await _operation_of(registry_db, seeded["draft"])
    ttl = BrokerConfig().discovery_cache_ttl_s
    start = clock.now  # the entry expires at exactly ``start + ttl``
    assert await _served_operation(resolver) == live_op

    await RevisionService(integration_context).promote(
        VENDOR, NAME, VERSION, str(seeded["draft"]), identity=OPERATOR
    )

    clock.now = start + ttl - 0.1
    assert await _served_operation(resolver) == live_op, "stale for the whole TTL"
    clock.now = start + ttl
    assert await _served_operation(resolver) == draft_op


async def test_deployed_broker_keeps_routing_a_deleted_api_until_the_ttl_lapses(
    integration_context: Context, registry_db: DatabaseSession, seeded: dict[str, uuid.UUID]
) -> None:
    """The route (and so the API's bound credentials) outlives a delete by up to the TTL."""
    clock = _Clock()
    resolver = _deployed_broker_resolver(registry_db, clock)
    live_op = await _operation_of(registry_db, seeded["live"])
    ttl = BrokerConfig().discovery_cache_ttl_s
    start = clock.now  # the entry expires at exactly ``start + ttl``
    assert await _served_operation(resolver) == live_op

    await ApiService(integration_context).delete(VENDOR, NAME, VERSION, identity=OPERATOR)

    clock.now = start + ttl - 0.1
    assert await _served_operation(resolver) == live_op
    clock.now = start + ttl
    assert await _served_operation(resolver) is None


# --- Writer in the broker's process: invalidation on commit -----------------


async def test_promote_invalidates_cached_route(
    integration_context: Context, registry_db: DatabaseSession, seeded: dict[str, uuid.UUID]
) -> None:
    resolver = _cached_resolver(registry_db)
    assert await _served_operation(resolver) == await _operation_of(registry_db, seeded["live"])

    await RevisionService(integration_context).promote(
        VENDOR, NAME, VERSION, str(seeded["draft"]), identity=OPERATOR
    )

    assert await _served_operation(resolver) == await _operation_of(registry_db, seeded["draft"])


async def test_archive_invalidates_cached_route(
    integration_context: Context, registry_db: DatabaseSession, clean_registry: None
) -> None:
    api_id = await _seed_api(registry_db)
    # Only a draft or a catalog-imported revision may be archived.
    live = await _seed_revision(
        registry_db, api_id, state=ApiRevisionState.IMPORTED, make_current=True
    )
    resolver = _cached_resolver(registry_db)
    assert await _served_operation(resolver) is not None

    await RevisionService(integration_context).archive(
        VENDOR, NAME, VERSION, str(live), identity=OPERATOR
    )

    assert await _served_operation(resolver) is None


async def test_api_delete_invalidates_cached_route(
    integration_context: Context, registry_db: DatabaseSession, seeded: dict[str, uuid.UUID]
) -> None:
    resolver = _cached_resolver(registry_db)
    assert await _served_operation(resolver) is not None

    await ApiService(integration_context).delete(VENDOR, NAME, VERSION, identity=OPERATOR)

    assert await _served_operation(resolver) is None


async def test_overlay_rollback_invalidates_cached_route(
    integration_context: Context, registry_db: DatabaseSession, clean_registry: None
) -> None:
    """The rollback's compare-and-set of the live revision re-routes cached URLs."""
    api_id = await _seed_api(registry_db)
    base = await _seed_revision(
        registry_db,
        api_id,
        state=ApiRevisionState.ARCHIVED,
        make_current=False,
        origin=ORIGIN_CATALOG,
    )
    overlaid = await _seed_revision(
        registry_db, api_id, state=ApiRevisionState.IMPORTED, make_current=True, origin="overlay"
    )
    async with registry_db.session() as session:
        overlay = Overlay(
            api_id=api_id,
            document={"overlay": "1.0.0", "actions": []},
            status="confirmed",
            confirmed_revision_id=overlaid,
            superseded_revision_id=base,
            created_by="usr_test",
        )
        session.add(overlay)
        await session.commit()
        overlay_id = overlay.id
    resolver = _cached_resolver(registry_db)
    assert await _served_operation(resolver) == await _operation_of(registry_db, overlaid)

    await OverlayService(integration_context).rollback(
        VENDOR, NAME, VERSION, overlay_id, identity=OPERATOR
    )

    assert await _served_operation(resolver) == await _operation_of(registry_db, base)


def _import_spec(summary: str, *, sha: str) -> IngestSpecification:
    content = {
        "openapi": "3.1.0",
        "info": {"title": "Routes", "version": VERSION},
        "servers": [{"url": f"https://{HOST}"}],
        "paths": {
            PATH: {
                "get": {
                    "operationId": "listWidgets",
                    "summary": summary,
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    return IngestSpecification(
        spec_type=SpecType.OPENAPI,
        api_identifier=ApiIdentifier(vendor=VENDOR, name=NAME, version=VERSION),
        sha=sha,
        content=content,
        source_type=ApiRevisionSourceType.INLINE,
        source_filename="openapi.json",
        submitted_by="usr_test",
        origin=ORIGIN_CATALOG,
    )


async def test_import_invalidates_cached_route_and_cached_miss(
    integration_context: Context, registry_db: DatabaseSession, clean_registry: None
) -> None:
    """A catalog import goes live in the ingest transaction; its commit re-routes."""
    resolver = _cached_resolver(registry_db, discovery_cache_negative_ttl_s=300.0)
    assert await _served_operation(resolver) is None  # cached as a miss

    first = await Ingestor(integration_context).ingest(
        _import_spec("List widgets", sha="sha-v1"), created_by="usr_test"
    )
    assert await _served_operation(resolver) == await _operation_of(registry_db, first.revision_id)

    second = await Ingestor(integration_context).ingest(
        _import_spec("List widgets (v2)", sha="sha-v2"), created_by="usr_test"
    )
    assert second.revision_id != first.revision_id
    assert await _served_operation(resolver) == await _operation_of(registry_db, second.revision_id)


async def test_url_index_rewrite_invalidates_cached_route(
    registry_db: DatabaseSession, seeded: dict[str, uuid.UUID]
) -> None:
    resolver = _cached_resolver(registry_db)
    assert await _served_operation(resolver) is not None

    async with registry_db.transaction() as session:
        await UrlIndexRepository.delete_for_revision(session, seeded["live"])

    assert await _served_operation(resolver) is None


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


class _SavepointFailureError(Exception):
    pass


async def test_savepoint_rollback_keeps_the_outer_transactions_mark(
    registry_db: DatabaseSession, seeded: dict[str, uuid.UUID]
) -> None:
    """A rolled-back SAVEPOINT after a routing write must not cancel its invalidation."""
    start = route_generation()
    async with registry_db.transaction() as session:
        await ApiRepository.set_current_revision(session, seeded["api_id"], seeded["draft"])
        with pytest.raises(_SavepointFailureError):
            async with session.begin_nested():
                raise _SavepointFailureError
        assert route_generation() == start

    assert route_generation() == start + 1


async def test_savepoint_release_does_not_bump_before_the_outer_commit(
    registry_db: DatabaseSession, seeded: dict[str, uuid.UUID]
) -> None:
    """A routing write inside a SAVEPOINT is visible only once the outer transaction commits."""
    start = route_generation()
    async with registry_db.transaction() as session:
        async with session.begin_nested():
            await ApiRepository.set_current_revision(session, seeded["api_id"], seeded["draft"])
        assert route_generation() == start, "RELEASE SAVEPOINT must not invalidate"

    assert route_generation() == start + 1


class _OuterFailureError(Exception):
    pass


async def test_outer_rollback_after_a_savepoint_discards_the_mark(
    registry_db: DatabaseSession, seeded: dict[str, uuid.UUID]
) -> None:
    """A write released from a SAVEPOINT is undone by the outer rollback, so nothing bumps.

    ``transaction()`` opens a real outer transaction on every backend (``BEGIN
    IMMEDIATE`` on SQLite), so the RELEASE does not commit the write.
    """
    start = route_generation()
    with pytest.raises(_OuterFailureError):
        async with registry_db.transaction() as session:
            async with session.begin_nested():
                await ApiRepository.set_current_revision(session, seeded["api_id"], seeded["draft"])
            assert route_generation() == start, "RELEASE SAVEPOINT must not invalidate"
            raise _OuterFailureError

    assert route_generation() == start
    async with registry_db.session() as session:
        stmt = select(Api.current_revision_id).where(Api.id == seeded["api_id"])
        assert (await session.execute(stmt)).scalar_one() == seeded["live"]


# --- Through the execute route: pins are checked per caller -----------------


class _ReachedAuthorizationError(Exception):
    """Stops ``_handle`` at authorization, carrying the route discovery handed it."""

    def __init__(self, operation_id: str | None, api: APIReference, path: str) -> None:
        super().__init__(operation_id)
        self.operation_id = operation_id
        self.api = api
        self.path = path


@pytest.fixture()
def route_harness(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run ``_handle`` up to authorization without DNS.

    The test host does not resolve, so the egress validator passes the URL
    through (it is covered by its own tests); authorization records the
    operation, API and rule path discovery handed it and stops the request there.
    """

    async def _validate(raw_url: str, egress: Any = None) -> str:
        return raw_url

    async def _authorize(**kwargs: Any) -> Any:
        raise _ReachedAuthorizationError(kwargs["operation_id"], kwargs["api"], kwargs["path"])

    monkeypatch.setattr(execute_mod, "validate_upstream_url_async", _validate)
    monkeypatch.setattr(execute_mod, "authorize_execution", _authorize)


async def _execute(
    ctx: Context, resolver: InProcessRegistryResolver, *, url: str, sub: str, pin: str | None
) -> _ReachedAuthorizationError:
    """Drive the broker execute route's ``_handle``; returns what reached authorization."""
    app = FastAPI()
    app.state.broker_registry_resolver = resolver
    path = "/" + url.removeprefix("https://")
    headers = [] if pin is None else [(b"jentic-revision", pin.encode())]
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers,
            "app": app,
        }
    )
    try:
        await execute_mod._handle(
            request,
            "GET",
            ctx,
            Identity(sub=sub),
            MagicMock(),  # credential_deriver: authorization is stubbed
            MagicMock(),  # agent_rule_evaluator
            MagicMock(),  # runner: never reached
            None,  # idempotency
        )
    except _ReachedAuthorizationError as reached:
        return reached
    raise AssertionError("_handle returned without reaching authorization")


SEEDED_API = APIReference(vendor=VENDOR, name=NAME, version=VERSION)


@pytest.mark.usefixtures("route_harness")
async def test_cached_pinned_route_never_serves_another_users_pin(
    integration_context: Context, registry_db: DatabaseSession, seeded: dict[str, uuid.UUID]
) -> None:
    """The live revision serves the URL; the owner's pinned call caches the draft's route."""
    resolver = _cached_resolver(registry_db)
    cache = resolver._discovery_cache
    assert cache is not None
    pin = f"{VENDOR}:{NAME}:{VERSION}=rev_{seeded['draft'].hex}"
    live_op = await _operation_of(registry_db, seeded["live"])
    draft_op = await _operation_of(registry_db, seeded["draft"])

    owner = await _execute(integration_context, resolver, url=URL, sub=OWNER, pin=pin)
    assert owner.operation_id == draft_op
    assert len(cache) == 2  # the live route and the owner's pinned route

    warm_owner = await _execute(integration_context, resolver, url=URL, sub=OWNER, pin=pin)
    assert (warm_owner.operation_id, warm_owner.api, warm_owner.path) == (
        draft_op,
        SEEDED_API,
        PATH,
    )

    with (
        _count_statements(registry_db) as pin_check,
        pytest.raises(UnauthorizedRevisionPinError) as denied,
    ):
        await _execute(integration_context, resolver, url=URL, sub=OTHER, pin=pin)
    assert denied.value.type == "unauthorized_revision_pin"
    assert pin_check, "the pin check reads the registry even with the route cached"

    other = await _execute(integration_context, resolver, url=URL, sub=OTHER, pin=None)
    assert (other.operation_id, other.api, other.path) == (live_op, SEEDED_API, PATH)
    assert len(cache) == 2, "every call after the owner's first was a cache hit"


@pytest.mark.usefixtures("route_harness")
async def test_cached_draft_only_route_never_serves_another_users_pin(
    integration_context: Context, registry_db: DatabaseSession, seeded: dict[str, uuid.UUID]
) -> None:
    """Only the draft serves the URL; the owner's call caches it through the pin fallback.

    The fallback skips a pin its caller may not use, so another user gets the
    ordinary ``operation_not_found``, never the owner's cached draft route.
    """
    draft = await _seed_revision(
        registry_db,
        seeded["api_id"],
        state=ApiRevisionState.DRAFT,
        make_current=False,
        path=DRAFT_ONLY_PATH,
    )
    resolver = _cached_resolver(registry_db)
    pin = f"{VENDOR}:{NAME}:{VERSION}=rev_{draft.hex}"

    owner = await _execute(integration_context, resolver, url=DRAFT_ONLY_URL, sub=OWNER, pin=pin)
    assert owner.operation_id == await _operation_of(registry_db, draft)
    warm_owner = await _execute(
        integration_context, resolver, url=DRAFT_ONLY_URL, sub=OWNER, pin=pin
    )
    assert (warm_owner.operation_id, warm_owner.api, warm_owner.path) == (
        owner.operation_id,
        SEEDED_API,
        DRAFT_ONLY_PATH,
    )

    for other_pin in (pin, None):
        with pytest.raises(OperationNotFoundError) as missing:
            await _execute(
                integration_context, resolver, url=DRAFT_ONLY_URL, sub=OTHER, pin=other_pin
            )
        assert missing.value.type == "operation_not_found"
