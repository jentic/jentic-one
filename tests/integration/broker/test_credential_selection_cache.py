"""Credential-selection cache in front of ``CredentialResolver.resolve`` (#1714).

Seeds credentials in the real control DB and records every statement the
control engine issues. Pins that a warm resolve skips the vendor header query,
that a committed credential write in this process takes effect on the next
resolve, that a change the process did not see (another process's write) is
caught by the winner's material read, that the caller's binding boundary is
applied on every call, and that the cache never holds secret material.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import AsyncGenerator, Collection, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import delete, event, update

from jentic_one.broker.core.exceptions import CredentialNotProvisionedError as Domain424
from jentic_one.broker.services.credentials.errors import CredentialNotProvisionedError
from jentic_one.broker.services.credentials.orchestrator import CredentialService
from jentic_one.broker.services.credentials.resolver import CredentialResolver, ResolvedCredential
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.control.repos import CredentialRepository
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.credential_selection_cache import (
    CredentialHeader,
    CredentialSelectionCache,
)
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType, StoredCredentialType
from jentic_one.shared.schemas import APIReference

pytestmark = pytest.mark.integration

_VENDOR = "cache-1714-vendor"
_API = APIReference(vendor=_VENDOR, name="payments", version="v1")
_HEADER_FROM = re.compile(r'\bFROM\s+(?:"?\w+"?\.)?"?credentials"?(?:\s|$)', re.IGNORECASE)
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
    return CredentialSelectionCache(ttl_seconds=60.0)


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
    return [s for s in statements if _HEADER_FROM.search(s)]


async def _seed(
    ctx: Context,
    *,
    cred_id: str,
    stored_type: StoredCredentialType = StoredCredentialType.NO_AUTH,
    name: str | None = None,
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


async def _unseen_update(ctx: Context, **values: Any) -> None:
    """Update ``cred_1714_key`` without going through the ORM's write events."""
    stmt = update(Credential).where(Credential.id == "cred_1714_key").values(**values)
    async with ctx.control_db.session() as session:
        conn = await session.connection()
        await conn.execute(stmt)
        await session.commit()


async def _resolve(
    ctx: Context,
    cache: CredentialSelectionCache | None,
    *,
    allowed: Collection[str] | None = None,
) -> tuple[ResolvedCredential, list[str]]:
    with _record(ctx.control_db) as statements:
        resolved = await CredentialResolver(ctx, cache=cache).resolve(
            api=_API, caller="agent_a", allowed_credential_ids=allowed
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
    cache = CredentialSelectionCache(ttl_seconds=5.0)
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
    cache = CredentialSelectionCache(ttl_seconds=0)

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
    """A deleted credential stops resolving immediately, by instance or bulk delete."""
    await _seed(integration_context, cred_id="cred_1714_one")
    await _seed(integration_context, cred_id="cred_1714_two")
    await _resolve(integration_context, cache, allowed={"cred_1714_one"})

    async with integration_context.control_db.transaction() as session:
        assert await CredentialRepository.delete(session, "cred_1714_one")
    with pytest.raises(CredentialNotProvisionedError):
        await _resolve(integration_context, cache, allowed={"cred_1714_one"})

    await _resolve(integration_context, cache, allowed={"cred_1714_two"})
    async with integration_context.control_db.transaction() as session:
        await session.execute(delete(Credential).where(Credential.id == "cred_1714_two"))
    with pytest.raises(CredentialNotProvisionedError):
        await _resolve(integration_context, cache, allowed={"cred_1714_two"})


async def test_fill_racing_an_invalidation_is_dropped(
    integration_context: Context, clean_vendor: None, cache: CredentialSelectionCache
) -> None:
    """A fill that read the DB before an invalidation is not stored."""
    await _seed(integration_context, cred_id="cred_1714_one")
    generation = cache.generation

    async with integration_context.control_db.transaction() as session:
        await session.execute(
            update(Credential).where(Credential.id == "cred_1714_one").values(name="x")
        )
    cache.put(_VENDOR, (), generation=generation)

    assert cache.get(_VENDOR) is None


async def test_unseen_write_to_a_secret_bearing_winner_is_caught(
    integration_context: Context, clean_vendor: None, cache: CredentialSelectionCache
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
