"""Unit tests for the broker's short-TTL actor-status cache (the JWT path's liveness check)."""

from __future__ import annotations

import asyncio
import time

import jwt
import pytest

from jentic_one.broker.core.token_validation import CachedActorStatus
from jentic_one.broker.services.auth import JwtTokenValidator, JwtVerifier
from jentic_one.shared.auth.errors import TokenValidationError

_SECRET = "unit-test-secret-key-32-bytes-long!!"  # pragma: allowlist secret


class _StatusResolver:
    """Answers a fixed verdict and counts calls (no DB)."""

    def __init__(self, *, active: bool) -> None:
        self.active = active
        self.calls: list[tuple[str, str]] = []

    async def is_active(self, *, actor_id: str, actor_type: str) -> bool:
        self.calls.append((actor_type, actor_id))
        await asyncio.sleep(0)
        return self.active


def _token(sub: str = "agnt_x", **extra: object) -> str:
    claims: dict[str, object] = {
        "sub": sub,
        "exp": int(time.time()) + 60,
        "actor_type": "agent",
        "scopes": ["broker:execute"],
    }
    claims.update(extra)
    return jwt.encode(claims, _SECRET, algorithm="HS256")


async def test_verdict_is_cached_until_the_ttl_lapses() -> None:
    resolver = _StatusResolver(active=True)
    cache = CachedActorStatus(resolver=resolver, cache_ttl_seconds=0.05)

    assert await cache.is_active(actor_id="agnt_x", actor_type="agent") is True
    resolver.active = False
    # Inside the TTL the cached verdict stands.
    assert await cache.is_active(actor_id="agnt_x", actor_type="agent") is True
    assert len(resolver.calls) == 1

    await asyncio.sleep(0.06)
    assert await cache.is_active(actor_id="agnt_x", actor_type="agent") is False
    assert len(resolver.calls) == 2


async def test_concurrent_misses_share_one_lookup() -> None:
    resolver = _StatusResolver(active=True)
    cache = CachedActorStatus(resolver=resolver, cache_ttl_seconds=30)

    results = await asyncio.gather(
        *(cache.is_active(actor_id="agnt_x", actor_type="agent") for _ in range(10))
    )

    assert results == [True] * 10
    assert resolver.calls == [("agent", "agnt_x")]


async def test_cache_is_bounded() -> None:
    resolver = _StatusResolver(active=True)
    cache = CachedActorStatus(resolver=resolver, cache_ttl_seconds=30, max_entries=2)

    for actor_id in ("agnt_a", "agnt_b", "agnt_c"):
        await cache.is_active(actor_id=actor_id, actor_type="agent")
    # ``agnt_a`` was evicted, so asking again is a fresh lookup.
    await cache.is_active(actor_id="agnt_a", actor_type="agent")

    assert [c[1] for c in resolver.calls] == ["agnt_a", "agnt_b", "agnt_c", "agnt_a"]


def test_max_entries_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_entries"):
        CachedActorStatus(resolver=_StatusResolver(active=True), max_entries=0)


async def test_jwt_for_an_inactive_agent_is_refused_after_the_claim_checks() -> None:
    resolver = _StatusResolver(active=False)
    validator = JwtTokenValidator(verifier=JwtVerifier(secret=_SECRET), actor_status=resolver)

    with pytest.raises(TokenValidationError, match="jwt_actor_inactive"):
        await validator.validate(_token())
    assert resolver.calls == [("agent", "agnt_x")]

    # A token that fails a claim check is refused without a status lookup.
    with pytest.raises(TokenValidationError, match="jwt_actor_type_unknown"):
        await validator.validate(_token(actor_type="toolkit"))
    with pytest.raises(TokenValidationError):
        await validator.validate(_token() + "tampered")
    assert len(resolver.calls) == 1


async def test_jwt_for_an_active_agent_passes() -> None:
    resolver = _StatusResolver(active=True)
    validator = JwtTokenValidator(verifier=JwtVerifier(secret=_SECRET), actor_status=resolver)

    identity = await validator.validate(_token())

    assert identity.sub == "agnt_x"
    assert identity.permissions == ["broker:execute"]
