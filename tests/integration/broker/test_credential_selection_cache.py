"""Credential-selection cache in front of ``CredentialResolver.resolve`` (#1714).

Seeds credentials in the real control DB and records every statement the
control engine issues. Pins that a warm resolve skips the vendor header query
(also for a winner with no material row), that the sync execute route opts in,
that a credential write in this process (ORM, marked bulk or raw SQL) takes
effect on the next resolve once its outermost transaction ends (SAVEPOINTs
neither clear the cache early nor drop a pending clear), that a change the
process did not see (another process's write) is caught by the winner's
material read or, for a failed selection, by a fresh read, that a fill racing
a write is not stored and concurrent misses share one read, that the caller's
binding boundary is applied on every call, and that the cache never holds
secret material.
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
from collections.abc import AsyncGenerator, Collection, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI, Request
from sqlalchemy import delete, event, insert, update

from jentic_one.auth.repos.agent_purge_repo import AgentControlPurgeRepository
from jentic_one.broker.core.exceptions import CredentialNotProvisionedError as Domain424
from jentic_one.broker.services.credentials.errors import (
    AmbiguousCredentialError,
    CredentialIdNotFoundError,
    CredentialNotProvisionedError,
)
from jentic_one.broker.services.credentials.orchestrator import CredentialService
from jentic_one.broker.services.credentials.resolver import CredentialResolver, ResolvedCredential
from jentic_one.broker.web.routers import execute as execute_mod
from jentic_one.control.core.schema.connect_sessions import ConnectSession
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.control.repos import CredentialRepository
from jentic_one.registry.repos.control_credential_boundary_repo import (
    ControlCredentialBoundaryRepository,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import (
    BoundCredential,
    CredentialDerivation,
    ResolveResult,
)
from jentic_one.shared.context import Context
from jentic_one.shared.credential_selection_cache import (
    CredentialHeader,
    CredentialSelectionCache,
    mark_credentials_changed,
)
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType, StoredCredentialType
from jentic_one.shared.schemas import APIReference, OperationInfo

pytestmark = pytest.mark.integration

_VENDOR = "cache-1714-vendor"
_API = APIReference(vendor=_VENDOR, name="payments", version="v1")
# The vendor candidate query (``list_by_vendor``), not the winner's by-id read.
_VENDOR_QUERY = re.compile(r'"?credentials"?\.api_vendor\s+IN\b', re.IGNORECASE)
_SECRET = "plaintext-1714-secret"  # pragma: allowlist secret

# Every field the cache may hold — non-secret header columns only.
_HEADER_FIELDS = {
    "id",
    "name",
    "type",
    "provider",
    "api_vendor",
    "api_name",
    "api_version",
    "server_variables",
    "active",
    "state",
    "created_by",
    "created_at",
    "provider_account_ref",
}


@pytest.fixture()
async def clean_vendor(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async def _truncate() -> None:
        async with control_db.session() as session:
            await session.execute(delete(Credential).where(Credential.api_vendor == _VENDOR))
            await session.commit()

    await _truncate()
    yield
    await _truncate()


@pytest.fixture()
def cache() -> CredentialSelectionCache:
    return CredentialSelectionCache(ttl_seconds=60.0, max_headers=1_000)


class _Counter:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any] | None] = []

    def add(self, amount: int, attributes: dict[str, Any] | None = None) -> None:
        self.calls.extend([attributes] * amount)


@pytest.fixture()
def counters(monkeypatch: pytest.MonkeyPatch) -> dict[str, _Counter]:
    """The cache's counters, recorded in place of the OTel instruments."""
    recorded = {name: _Counter() for name in ("hits", "misses", "joined", "rechecks", "stale")}
    for name, counter in recorded.items():
        monkeypatch.setattr(f"jentic_one.shared.credential_selection_cache._cache_{name}", counter)
    return recorded


@contextmanager
def _record(db: DatabaseSession) -> Iterator[list[str]]:
    statements: list[str] = []

    def _on(_conn: Any, _cursor: Any, sql: str, *_rest: Any) -> None:
        statements.append(sql)

    sync_engine = db.engine.sync_engine
    event.listen(sync_engine, "before_cursor_execute", _on)
    try:
        yield statements
    finally:
        event.remove(sync_engine, "before_cursor_execute", _on)


def _headers(statements: list[str]) -> list[str]:
    return [s for s in statements if _VENDOR_QUERY.search(s)]


async def _seed(
    ctx: Context,
    *,
    cred_id: str,
    stored_type: StoredCredentialType = StoredCredentialType.NO_AUTH,
    name: str | None = None,
    state: str = "connected",
    provider_account_ref: str | None = None,
) -> None:
    async with ctx.control_db.transaction() as session:
        session.add(
            Credential(
                id=cred_id,
                type=stored_type,
                name=name or f"name-{cred_id}",
                api_vendor=_VENDOR,
                api_name=_API.name,
                api_version=_API.version,
                created_by="usr_owner",
                state=state,
                provider_account_ref=provider_account_ref,
            )
        )
        if stored_type == StoredCredentialType.API_KEY:
            await session.flush()
            session.add(
                CustomerAPIKey(
                    credential_id=cred_id,
                    encrypted_key=ctx.encryption.encrypt(_SECRET),
                    location="header",
                    field_name="X-Api-Key",
                )
            )


async def _unseen(ctx: Context, stmt: Any) -> None:
    """Run ``stmt`` on the raw connection, unseen by the in-process invalidation.

    Stands in for a write served by another process.
    """
    async with ctx.control_db.session() as session:
        conn = await session.connection()
        await conn.execute(stmt)
        await session.commit()


async def _unseen_update(ctx: Context, cred_id: str = "cred_1714_key", **values: Any) -> None:
    await _unseen(ctx, update(Credential).where(Credential.id == cred_id).values(**values))


async def _unseen_insert(ctx: Context, cred_id: str, **values: Any) -> None:
    row = {
        "id": cred_id,
        "type": StoredCredentialType.NO_AUTH,
        "name": f"name-{cred_id}",
        "provider": "static",
        "api_vendor": _VENDOR,
        "api_name": _API.name,
        "api_version": _API.version,
        "created_by": "usr_owner",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        **values,
    }
    await _unseen(ctx, insert(Credential).values(**row))


async def _resolve(
    ctx: Context,
    cache: CredentialSelectionCache | None,
    *,
    allowed: Collection[str] | None = None,
    credential_id: str | None = None,
    credential_name: str | None = None,
) -> tuple[ResolvedCredential, list[str]]:
    with _record(ctx.control_db) as statements:
        resolved = await CredentialResolver(ctx, cache=cache).resolve(
            api=_API,
            caller="agent_a",
            allowed_credential_ids=allowed,
            credential_id=credential_id,
            credential_name=credential_name,
        )
    return resolved, statements


@pytest.mark.parametrize(
    ("stored_type", "warm_statements"),
    [(StoredCredentialType.NO_AUTH, 0), (StoredCredentialType.API_KEY, 1)],
    ids=["no_auth", "api_key"],
)
async def test_warm_resolve_skips_the_vendor_header_query(
    integration_context: Context,
    clean_vendor: None,
    cache: CredentialSelectionCache,
    stored_type: StoredCredentialType,
    warm_statements: int,
) -> None:
    """Miss: header query (+ material). Hit: no header query; material still read."""
    await _seed(integration_context, cred_id="cred_1714_one", stored_type=stored_type)

    cold, cold_statements = await _resolve(integration_context, cache)
    warm, statements = await _resolve(integration_context, cache)

    assert len(_headers(cold_statements)) == 1, cold_statements
    assert _headers(statements) == [], statements
    assert len(statements) == warm_statements, statements
    assert warm == cold
    if stored_type == StoredCredentialType.API_KEY:
        # Material comes from the DB on the hit too, never from the cache.
        assert "customer_api_keys" in statements[0]
        assert warm.encrypted_secret is not None


async def test_expired_entry_misses(
    integration_context: Context, clean_vendor: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Past the TTL the next resolve reads the headers again."""
    await _seed(integration_context, cred_id="cred_1714_one")
    cache = CredentialSelectionCache(ttl_seconds=5.0, max_headers=1_000)
    now = [1000.0]
    monkeypatch.setattr(
        "jentic_one.shared.credential_selection_cache.time.monotonic", lambda: now[0]
    )

    await _resolve(integration_context, cache)
    now[0] += 4.9
    _, fresh = await _resolve(integration_context, cache)
    now[0] += 0.2
    _, expired = await _resolve(integration_context, cache)

    assert _headers(fresh) == []
    assert len(_headers(expired)) == 1, expired


async def test_ttl_zero_disables_the_cache(
    integration_context: Context, clean_vendor: None
) -> None:
    """``ttl_seconds=0`` reads the headers on every resolve and stores nothing."""
    await _seed(integration_context, cred_id="cred_1714_one")
    cache = CredentialSelectionCache(ttl_seconds=0, max_headers=1_000)

    for _ in range(2):
        _, statements = await _resolve(integration_context, cache)
        assert len(_headers(statements)) == 1, statements
    assert cache.get(_VENDOR) is None


async def test_credential_update_takes_effect_on_next_resolve(
    integration_context: Context, clean_vendor: None, cache: CredentialSelectionCache
) -> None:
    """A committed rename, then disable, in this process is seen without waiting out the TTL."""
    await _seed(integration_context, cred_id="cred_1714_one")
    await _resolve(integration_context, cache)

    async with integration_context.control_db.transaction() as session:
        await CredentialRepository.update_header(session, "cred_1714_one", name="renamed")
    renamed, statements = await _resolve(integration_context, cache)
    assert renamed.name == "renamed"
    assert len(_headers(statements)) == 1, statements

    async with integration_context.control_db.transaction() as session:
        await CredentialRepository.update_header(session, "cred_1714_one", active=False)
    with pytest.raises(CredentialNotProvisionedError):
        await _resolve(integration_context, cache)


async def test_credential_delete_takes_effect_on_next_resolve(
    integration_context: Context, clean_vendor: None, cache: CredentialSelectionCache
) -> None:
    """A deleted credential stops resolving immediately: by instance, or a marked bulk delete."""
    await _seed(integration_context, cred_id="cred_1714_one")
    await _seed(integration_context, cred_id="cred_1714_two")
    await _resolve(integration_context, cache, allowed={"cred_1714_one"})

    async with integration_context.control_db.transaction() as session:
        assert await CredentialRepository.delete(session, "cred_1714_one")
    with pytest.raises(CredentialNotProvisionedError):
        await _resolve(integration_context, cache, allowed={"cred_1714_one"})

    await _resolve(integration_context, cache, allowed={"cred_1714_two"})
    async with integration_context.control_db.transaction() as session:
        # A bulk statement is invisible to the ORM write events; its caller marks.
        mark_credentials_changed(session)
        await session.execute(delete(Credential).where(Credential.id == "cred_1714_two"))
    with pytest.raises(CredentialNotProvisionedError):
        await _resolve(integration_context, cache, allowed={"cred_1714_two"})


async def test_unseen_write_to_a_secret_bearing_winner_is_caught(
    integration_context: Context,
    clean_vendor: None,
    cache: CredentialSelectionCache,
    counters: dict[str, _Counter],
) -> None:
    """A change no hook saw (another process's write) is caught by the material read.

    A Core statement on the raw connection bypasses the in-process
    invalidation, standing in for a write served by another process. The
    cached winner's material read returns its current header, which differs,
    so the resolver re-reads the vendor's headers.
    """
    await _seed(
        integration_context, cred_id="cred_1714_key", stored_type=StoredCredentialType.API_KEY
    )
    await _resolve(integration_context, cache)

    await _unseen_update(integration_context, name="edited")
    edited, statements = await _resolve(integration_context, cache)
    assert edited.name == "edited"
    assert len(_headers(statements)) == 1, statements
    # A stale hit is reported as stale, never as a hit.
    assert counters["hits"].calls == []
    assert counters["stale"].calls == [{"reason": "winner_changed"}]

    await _unseen_update(integration_context, active=False)
    with pytest.raises(CredentialNotProvisionedError):
        await _resolve(integration_context, cache)


async def test_binding_change_applies_on_every_call(
    integration_context: Context, clean_vendor: None, cache: CredentialSelectionCache
) -> None:
    """The binding boundary is not cached: a removed binding denies on the next call."""
    await _seed(integration_context, cred_id="cred_1714_one")
    await _resolve(integration_context, cache, allowed={"cred_1714_one"})

    with pytest.raises(CredentialNotProvisionedError):
        await _resolve(integration_context, cache, allowed=set())


async def test_cached_selection_is_isolated_across_agents(
    integration_context: Context, clean_vendor: None
) -> None:
    """Agent B never gets agent A's credential from a cache agent A warmed.

    Both credentials cover the API equally; each agent is bound to its own. The
    sync path's service reads the shared cache, and each agent's own bindings
    still decide the result, including an agent bound to nothing.
    """
    await _seed(integration_context, cred_id="cred_1714_a")
    await _seed(integration_context, cred_id="cred_1714_b")
    service = CredentialService(integration_context, use_selection_cache=True)

    async def select(agent: str, allowed: set[str]) -> str:
        identity = Identity(sub=agent, actor_type=ActorType.AGENT, permissions=[], active=True)
        resolved = await service.select(
            api_vendor=_API.vendor,
            api_name=_API.name,
            api_version=_API.version,
            identity=identity,
            allowed_credential_ids=allowed,
        )
        assert resolved is not None
        return resolved.credential_id

    assert await select("agent_a", {"cred_1714_a"}) == "cred_1714_a"
    assert integration_context.credential_selection_cache.get(_VENDOR) is not None
    with _record(integration_context.control_db) as statements:
        assert await select("agent_b", {"cred_1714_b"}) == "cred_1714_b"
    assert _headers(statements) == [], statements
    with pytest.raises(Domain424):
        await select("agent_c", set())


async def test_default_service_does_not_use_the_cache(
    integration_context: Context, clean_vendor: None
) -> None:
    """Without the opt-in (the queued worker's path) resolution never fills the cache."""
    await _seed(integration_context, cred_id="cred_1714_one")
    identity = Identity(sub="agent_a", actor_type=ActorType.AGENT, permissions=[], active=True)

    await CredentialService(integration_context).select(
        api_vendor=_API.vendor, api_name=_API.name, api_version=_API.version, identity=identity
    )

    assert integration_context.credential_selection_cache.get(_VENDOR) is None


async def test_cached_values_hold_no_secret_material(
    integration_context: Context, clean_vendor: None, cache: CredentialSelectionCache
) -> None:
    """Only non-secret ``CredentialHeader`` fields are cached — no ciphertext or plaintext."""
    await _seed(
        integration_context, cred_id="cred_1714_key", stored_type=StoredCredentialType.API_KEY
    )
    resolved, _ = await _resolve(integration_context, cache)
    assert resolved.encrypted_secret is not None

    cached = cache.get(_VENDOR)
    assert cached is not None and len(cached) == 1
    (header,) = cached
    assert type(header) is CredentialHeader
    assert {f.name for f in dataclasses.fields(header)} == _HEADER_FIELDS
    values = [str(getattr(header, f)) for f in _HEADER_FIELDS]
    for value in values:
        assert _SECRET not in value
        assert resolved.encrypted_secret not in value


async def test_warm_winner_without_a_material_row_reads_one_statement(
    integration_context: Context,
    clean_vendor: None,
    cache: CredentialSelectionCache,
    counters: dict[str, _Counter],
) -> None:
    """A managed-provider OAuth credential (no token row) stays cached, at one statement.

    Such a credential keeps only ``provider_account_ref``; its missing child row
    is a valid state, so the warm read must neither treat it as stale nor drop
    the vendor's entry for every other caller.
    """
    await _seed(
        integration_context,
        cred_id="cred_1714_managed",
        stored_type=StoredCredentialType.OAUTH2_AUTHORIZATION_CODE,
        provider_account_ref="apn_1714",
    )
    cold, cold_statements = await _resolve(integration_context, cache)
    for _ in range(2):
        warm, statements = await _resolve(integration_context, cache)
        assert len(statements) == 1, statements
        assert _headers(statements) == [], statements
        assert warm == cold
        assert cache.get(_VENDOR) is not None
    assert len(cold_statements) == 2, cold_statements
    assert (len(counters["hits"].calls), counters["stale"].calls) == (2, [])
    assert warm.provider_account_ref == "apn_1714"
    assert warm.encrypted_access_token is None


@pytest.mark.parametrize("by_id", [False, True], ids=["no_match", "credential_id"])
async def test_a_cached_negative_selection_is_rechecked_against_the_db(
    integration_context: Context,
    clean_vendor: None,
    cache: CredentialSelectionCache,
    counters: dict[str, _Counter],
    by_id: bool,
) -> None:
    """424 / unknown id from cached headers re-reads the DB, so connect-then-retry works.

    The new credential is inserted on the raw connection, unseen by this
    process's invalidation, standing in for a credential connected through
    another process.
    """
    expected: type[Exception] = CredentialNotProvisionedError
    target = "cred_1714_new"
    if by_id:
        await _seed(integration_context, cred_id="cred_1714_old")
        expected = CredentialIdNotFoundError
    with pytest.raises(expected):
        await _resolve(integration_context, cache, credential_id=target if by_id else None)
    assert cache.get(_VENDOR) is not None

    await _unseen_insert(integration_context, target)
    counters["stale"].calls.clear()
    counters["rechecks"].calls.clear()
    resolved, statements = await _resolve(
        integration_context, cache, credential_id=target if by_id else None
    )
    assert resolved.credential_id == target
    assert len(_headers(statements)) == 1, statements
    assert counters["rechecks"].calls == [{"reason": "negative_selection"}]
    # The re-read found a new row, so the cached headers were stale.
    assert counters["stale"].calls == [{"reason": "negative_selection"}]
    # The re-read refreshed the entry: the next call is a hit again.
    _, warm = await _resolve(integration_context, cache, credential_id=target)
    assert warm == []


async def test_a_persistent_negative_selection_is_a_recheck_not_stale(
    integration_context: Context,
    clean_vendor: None,
    cache: CredentialSelectionCache,
    counters: dict[str, _Counter],
) -> None:
    """A genuine, repeated 409 re-reads each time but never counts as a stale entry."""
    await _seed(integration_context, cred_id="cred_1714_a")
    await _seed(integration_context, cred_id="cred_1714_b")
    for _ in range(3):
        with pytest.raises(AmbiguousCredentialError):
            await _resolve(integration_context, cache)

    assert counters["rechecks"].calls == [{"reason": "negative_selection"}] * 2
    assert counters["stale"].calls == []
    assert counters["hits"].calls == []


async def test_a_bound_credential_missing_from_the_cache_forces_a_fresh_read(
    integration_context: Context,
    clean_vendor: None,
    cache: CredentialSelectionCache,
    counters: dict[str, _Counter],
) -> None:
    """A pin created by another process wins on the next call once the caller is bound to it.

    The cache is warmed with a vendor-wide credential; a more specific pin is
    inserted unseen. Bound to both, the caller's boundary names an id the
    cached headers lack, so the resolver re-reads and the pin wins.
    """
    await _unseen_insert(integration_context, "cred_1714_wide", api_name=None, api_version=None)
    allowed = {"cred_1714_wide"}
    assert (await _resolve(integration_context, cache, allowed=allowed))[0].credential_id == (
        "cred_1714_wide"
    )
    _, warm = await _resolve(integration_context, cache, allowed=allowed)
    assert _headers(warm) == []

    await _unseen_insert(integration_context, "cred_1714_pin")
    allowed = {"cred_1714_wide", "cred_1714_pin"}
    resolved, statements = await _resolve(integration_context, cache, allowed=allowed)
    assert resolved.credential_id == "cred_1714_pin"
    assert len(_headers(statements)) == 1, statements
    assert counters["rechecks"].calls == [{"reason": "boundary_mismatch"}]
    assert counters["stale"].calls == [{"reason": "boundary_mismatch"}]

    # The re-read refreshed the entry: the same boundary is a hit again.
    _, warm = await _resolve(integration_context, cache, allowed=allowed)
    assert _headers(warm) == []


async def test_a_bound_credential_cached_as_unusable_forces_a_fresh_read(
    integration_context: Context, clean_vendor: None, cache: CredentialSelectionCache
) -> None:
    """A sibling re-enabled elsewhere makes the call ambiguous on the next call."""
    await _seed(integration_context, cred_id="cred_1714_a")
    await _seed(integration_context, cred_id="cred_1714_b")
    await _unseen_update(integration_context, "cred_1714_b", active=False)
    await _resolve(integration_context, cache)  # fill while b is disabled
    assert (await _resolve(integration_context, cache))[0].credential_id == "cred_1714_a"

    await _unseen_update(integration_context, "cred_1714_b", active=True)
    with pytest.raises(AmbiguousCredentialError):
        await _resolve(integration_context, cache, allowed={"cred_1714_a", "cred_1714_b"})


async def test_a_boundary_id_the_db_lacks_rereads_without_counting_stale(
    integration_context: Context,
    clean_vendor: None,
    cache: CredentialSelectionCache,
    counters: dict[str, _Counter],
) -> None:
    """A deriver entry naming a since-deleted credential costs one header read, as uncached.

    The fresh headers match the cached ones, so the read is a recheck only.
    """
    await _seed(integration_context, cred_id="cred_1714_one")
    allowed = {"cred_1714_one", "cred_1714_gone"}
    await _resolve(integration_context, cache, allowed=allowed)
    for _ in range(2):
        resolved, statements = await _resolve(integration_context, cache, allowed=allowed)
        assert resolved.credential_id == "cred_1714_one"
        assert len(_headers(statements)) == 1, statements
    assert counters["rechecks"].calls == [{"reason": "boundary_mismatch"}] * 2
    assert counters["stale"].calls == []


async def test_cached_ambiguity_and_unknown_name_are_rechecked(
    integration_context: Context, clean_vendor: None, cache: CredentialSelectionCache
) -> None:
    """A stale 409 or unknown-name outcome is re-read; the fresh headers decide."""
    await _seed(integration_context, cred_id="cred_1714_a")
    await _seed(integration_context, cred_id="cred_1714_b")
    with pytest.raises(AmbiguousCredentialError):
        await _resolve(integration_context, cache)

    await _unseen(integration_context, delete(Credential).where(Credential.id == "cred_1714_b"))
    resolved, _ = await _resolve(integration_context, cache)
    assert resolved.credential_id == "cred_1714_a"

    await _unseen_update(integration_context, "cred_1714_a", name="renamed-elsewhere")
    resolved, _ = await _resolve(integration_context, cache, credential_name="renamed-elsewhere")
    assert resolved.credential_id == "cred_1714_a"


async def test_credential_state_gates_resolution_and_is_rechecked(
    integration_context: Context, clean_vendor: None, cache: CredentialSelectionCache
) -> None:
    """Only ``connected`` rows resolve, and a state change of the cached winner is seen."""
    await _seed(
        integration_context,
        cred_id="cred_1714_key",
        stored_type=StoredCredentialType.API_KEY,
        state="pending",
    )
    with pytest.raises(CredentialNotProvisionedError):
        await _resolve(integration_context, cache)

    await _unseen_update(integration_context, state="connected")
    resolved, _ = await _resolve(integration_context, cache)
    assert resolved.credential_id == "cred_1714_key"

    await _unseen_update(integration_context, state="failed")
    with pytest.raises(CredentialNotProvisionedError):
        await _resolve(integration_context, cache)


async def test_rolled_back_credential_write_still_invalidates(
    integration_context: Context, clean_vendor: None, cache: CredentialSelectionCache
) -> None:
    """A flushed write that is rolled back clears the cache (the safe direction)."""
    await _seed(integration_context, cred_id="cred_1714_one")
    await _resolve(integration_context, cache)

    async with integration_context.control_db.session() as session:
        await CredentialRepository.update_header(session, "cred_1714_one", name="never")
        await session.flush()
        await session.rollback()
    assert cache.get(_VENDOR) is None


async def test_savepoint_release_defers_invalidation_to_the_outer_commit(
    integration_context: Context, clean_vendor: None, cache: CredentialSelectionCache
) -> None:
    """A SAVEPOINT release is not the end of the write; the outer commit is."""
    await _seed(integration_context, cred_id="cred_1714_one")
    await _resolve(integration_context, cache)

    async with integration_context.control_db.session() as session:
        async with session.begin_nested():
            await CredentialRepository.update_header(session, "cred_1714_one", name="inner")
        assert cache.get(_VENDOR) is not None
        await session.commit()
    assert cache.get(_VENDOR) is None


async def test_rolled_back_savepoint_keeps_the_outer_pending_invalidation(
    integration_context: Context, clean_vendor: None, cache: CredentialSelectionCache
) -> None:
    """Rolling back a SAVEPOINT must not drop a credential write the outer transaction made."""
    await _seed(integration_context, cred_id="cred_1714_one")
    await _resolve(integration_context, cache)

    async with integration_context.control_db.session() as session:
        await CredentialRepository.update_header(session, "cred_1714_one", name="outer")
        await session.flush()
        nested = await session.begin_nested()
        await CredentialRepository.update_header(session, "cred_1714_one", name="inner")
        await session.flush()
        await nested.rollback()
        assert cache.get(_VENDOR) is not None
        await session.commit()
    assert cache.get(_VENDOR) is None
    renamed, _ = await _resolve(integration_context, cache)
    assert renamed.name == "outer"


async def test_raw_sql_credential_writers_invalidate(
    integration_context: Context, clean_vendor: None, cache: CredentialSelectionCache
) -> None:
    """The ``text()`` writers (agent purge, registry API delete) clear the cache on commit."""
    await _seed(integration_context, cred_id="cred_1714_one")
    await _seed(
        integration_context,
        cred_id="cred_1714_pending",
        stored_type=StoredCredentialType.OAUTH2_AUTHORIZATION_CODE,
        state="pending",
    )
    async with integration_context.control_db.transaction() as session:
        session.add(
            ConnectSession(
                id="cs_1714",
                credential_id="cred_1714_pending",
                vendor=_VENDOR,
                agent_id="agnt_1714",
                initiator_actor_id="agnt_1714",
                state="created",
                resolved_flow="authorization_code",
                poll_token_hash="p" * 64,
            )
        )
    await _resolve(integration_context, cache)

    async with integration_context.control_db.transaction() as session:
        counts = await AgentControlPurgeRepository.delete_for_agent(session, "agnt_1714")
    assert counts.pending_credentials == 1
    assert cache.get(_VENDOR) is None

    await _resolve(integration_context, cache)
    async with integration_context.control_db.transaction() as session:
        changed = await ControlCredentialBoundaryRepository.deactivate_credentials_for_api(
            session, api_vendor=_VENDOR, api_name=_API.name, api_version=_API.version
        )
    assert changed == 1
    assert cache.get(_VENDOR) is None
    with pytest.raises(CredentialNotProvisionedError):
        await _resolve(integration_context, cache)


async def test_resolver_fill_racing_a_write_is_not_stored(
    integration_context: Context,
    clean_vendor: None,
    cache: CredentialSelectionCache,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write that commits while the resolver's header read is in flight fences the fill.

    The resolver captures the cache generation before its header query; a write
    landing between that read and the store must leave the cache empty, or the
    pre-write headers would be served for the whole TTL.
    """
    await _seed(integration_context, cred_id="cred_1714_one")
    real_list = CredentialRepository.list_by_vendor

    async def list_then_write(session: Any, vendor: str) -> list[Credential]:
        rows = await real_list(session, vendor)
        async with integration_context.control_db.transaction() as writer:
            await CredentialRepository.update_header(writer, "cred_1714_one", name="racing")
        return rows

    monkeypatch.setattr(CredentialRepository, "list_by_vendor", staticmethod(list_then_write))
    resolved, _ = await _resolve(integration_context, cache)
    assert resolved.name == "name-cred_1714_one"
    assert cache.get(_VENDOR) is None

    monkeypatch.setattr(CredentialRepository, "list_by_vendor", staticmethod(real_list))
    fresh, _ = await _resolve(integration_context, cache)
    assert fresh.name == "racing"
    assert cache.get(_VENDOR) is not None


async def test_concurrent_misses_share_one_header_read(
    integration_context: Context,
    clean_vendor: None,
    cache: CredentialSelectionCache,
    monkeypatch: pytest.MonkeyPatch,
    counters: dict[str, _Counter],
) -> None:
    """After a clear, concurrent resolves of one vendor issue a single header query.

    The waiters are counted as joined, never as cache hits.
    """
    await _seed(integration_context, cred_id="cred_1714_one")
    real_list = CredentialRepository.list_by_vendor
    calls: list[str] = []
    release = asyncio.Event()

    async def gated_list(session: Any, vendor: str) -> list[Credential]:
        calls.append(vendor)
        await release.wait()
        return await real_list(session, vendor)

    monkeypatch.setattr(CredentialRepository, "list_by_vendor", staticmethod(gated_list))
    tasks = [
        asyncio.create_task(
            CredentialResolver(integration_context, cache=cache).resolve(
                api=_API, caller=f"agent_{i}"
            )
        )
        for i in range(4)
    ]
    await asyncio.sleep(0.05)
    release.set()
    results = await asyncio.gather(*tasks)

    assert calls == [_VENDOR]
    assert {r.credential_id for r in results} == {"cred_1714_one"}
    assert len(counters["joined"].calls) == 3
    assert counters["hits"].calls == []


async def test_sync_execute_route_selects_from_the_cache(
    integration_context: Context,
    clean_vendor: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sync execute route opts in: its second call issues no vendor header query.

    Runs the real route handler and authorization against the real control DB.
    Discovery, binding derivation and rule evaluation are stand-ins; the rule
    evaluator stops the request once the credential is selected.
    """
    await _seed(integration_context, cred_id="cred_1714_one")

    class _SelectedError(Exception):
        def __init__(self, credential_id: str) -> None:
            self.credential_id = credential_id

    class _Discovery:
        async def resolve_operation(
            self, *, method: str, url: str, revision_id: Any = None
        ) -> ResolveResult:
            return ResolveResult(operation=OperationInfo(id="op-1714"), api=_API, path_params={})

    class _Deriver:
        async def derive_credentials(self, **_: Any) -> CredentialDerivation:
            return CredentialDerivation(
                credentials=(BoundCredential(credential_id="cred_1714_one", rule_set_id=None),),
                agent_bound_any=True,
                api_served=True,
                identity_mismatch=None,
            )

    class _Evaluator:
        async def evaluate(self, *, credential_id: str, **_: Any) -> Any:
            raise _SelectedError(credential_id)

    async def _validated(raw_url: str, egress: Any = None) -> str:
        return raw_url

    monkeypatch.setattr(execute_mod, "validate_upstream_url_async", _validated)
    app = FastAPI()
    app.state.broker_registry_resolver = _Discovery()
    identity = Identity(sub="agnt_1714", actor_type=ActorType.AGENT, permissions=[], active=True)

    async def call() -> list[str]:
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api.example.com/v1/things",
                "raw_path": b"/api.example.com/v1/things",
                "query_string": b"",
                "headers": [],
                "app": app,
            }
        )
        with (
            _record(integration_context.control_db) as statements,
            pytest.raises(_SelectedError) as selected,
        ):
            await execute_mod._handle(
                request,
                "GET",
                integration_context,
                identity,
                _Deriver(),
                _Evaluator(),
                None,  # type: ignore[arg-type]
                None,
            )
        assert selected.value.credential_id == "cred_1714_one"
        return statements

    assert len(_headers(await call())) == 1
    assert await call() == []
