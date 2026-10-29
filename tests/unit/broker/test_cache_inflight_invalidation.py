"""Pins #1960: a broker cache invalidation that lands mid-lookup is not undone.

Each broker cache serves a revocation path (#1837): disabling an agent,
unbinding a credential, replacing rules. If the invalidation runs while a
lookup that began before it is still in flight, that lookup's result is
pre-revocation state and must not be stored, or the revoked access would live
on for a full TTL.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from jentic_one.broker.core.token_validation import CachedActorStatus, CachedTokenValidator
from jentic_one.broker.repos.caching_credential_deriver import CachingCredentialDeriver
from jentic_one.shared.auth.errors import TokenValidationError
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import BROKER_EXECUTE_PERMISSION
from jentic_one.shared.broker.protocols import BoundCredential, CredentialDerivation
from jentic_one.shared.models import ActorType

_LONG_TTL_S = 300.0


class _Gate:
    """Blocks the first lookup until released; later lookups run straight through."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def enter(self) -> None:
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            await self.release.wait()


def _identity(*, active: bool) -> Identity:
    return Identity(
        sub="agnt_x",
        actor_type=ActorType.AGENT,
        permissions=[BROKER_EXECUTE_PERMISSION],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        active=active,
    )


class _TokenResolver:
    def __init__(self, gate: _Gate) -> None:
        self.gate = gate
        self.active = True

    async def resolve_access_token(self, token: str) -> Identity | None:
        # Snapshot the state as of the read, the way a DB read would.
        active = self.active
        await self.gate.enter()
        return _identity(active=active)


@pytest.mark.asyncio
async def test_token_validator_invalidate_actor_mid_lookup_sticks() -> None:
    gate = _Gate()
    resolver = _TokenResolver(gate)
    validator = CachedTokenValidator(resolver=resolver, cache_ttl_seconds=_LONG_TTL_S)

    in_flight = asyncio.create_task(validator.validate("tok"))
    await gate.started.wait()
    resolver.active = False  # the agent is disabled ...
    validator.invalidate_actor("agnt_x")  # ... and the broker cache is told
    gate.release.set()
    assert (await in_flight).active is True  # the pre-disable read still answers its caller

    with pytest.raises(TokenValidationError, match="token_inactive"):
        await validator.validate("tok")
    assert gate.calls == 2


class _StatusResolver:
    def __init__(self, gate: _Gate) -> None:
        self.gate = gate
        self.active = True

    async def is_active(self, *, actor_id: str, actor_type: str) -> bool:
        active = self.active
        await self.gate.enter()
        return active


@pytest.mark.asyncio
async def test_actor_status_invalidate_actor_mid_lookup_sticks() -> None:
    gate = _Gate()
    resolver = _StatusResolver(gate)
    status = CachedActorStatus(resolver=resolver, cache_ttl_seconds=_LONG_TTL_S)

    in_flight = asyncio.create_task(status.is_active(actor_id="usr_1", actor_type="user"))
    await gate.started.wait()
    resolver.active = False
    status.invalidate_actor("usr_1")
    gate.release.set()
    assert await in_flight is True

    assert await status.is_active(actor_id="usr_1", actor_type="user") is False


class _Deriver:
    def __init__(self, gate: _Gate) -> None:
        self.gate = gate
        self.bound = True

    async def derive_credentials(
        self, *, agent_id: str, vendor: str, name: str, version: str
    ) -> CredentialDerivation:
        bound = self.bound
        await self.gate.enter()
        credentials = (BoundCredential(credential_id="cred_1", rule_set_id=None),) if bound else ()
        return CredentialDerivation(
            credentials=credentials,
            agent_bound_any=bound,
            api_served=True,
            identity_mismatch=None,
        )


@pytest.mark.asyncio
async def test_credential_deriver_invalidate_agent_mid_lookup_sticks() -> None:
    gate = _Gate()
    inner = _Deriver(gate)
    cached = CachingCredentialDeriver(inner, cache_ttl_seconds=_LONG_TTL_S)
    kwargs = {"agent_id": "agt_1", "vendor": "acme.com", "name": "pets", "version": "v1"}

    in_flight = asyncio.create_task(cached.derive_credentials(**kwargs))
    await gate.started.wait()
    inner.bound = False  # the binding is suspended
    cached.invalidate_agent("agt_1")
    gate.release.set()
    assert (await in_flight).credentials

    assert (await cached.derive_credentials(**kwargs)).credentials == ()
    assert gate.calls == 2
