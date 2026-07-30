"""Unit tests for the bounded LRU on the token-validation negative cache (§05 R3/R2.1)."""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime, timedelta

import pytest

from jentic_one.broker.core.token_validation import CachedTokenValidator
from jentic_one.shared.auth.errors import TokenValidationError
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorType
from jentic_one.shared.scopes import BROKER_EXECUTE_SCOPE


class _CountingResolver:
    """A protocol-conforming token resolver that counts calls (no DB)."""

    def __init__(self, *, resolved: Identity | None) -> None:
        self.resolved = resolved
        self.calls = 0

    async def resolve_access_token(self, token: str) -> Identity | None:
        self.calls += 1
        return self.resolved


def _identity() -> Identity:
    return Identity(
        sub="agnt_x",
        actor_type=ActorType.AGENT,
        permissions=[BROKER_EXECUTE_SCOPE],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        active=True,
    )


@pytest.mark.asyncio
async def test_negative_cache_is_bounded_by_max_entries() -> None:
    """A flood of unique garbage tokens never grows the cache past max_entries."""
    resolver = _CountingResolver(resolved=None)
    validator = CachedTokenValidator(resolver=resolver, cache_ttl_seconds=300.0, max_entries=10)

    for i in range(100):
        with pytest.raises(TokenValidationError, match="unknown_token"):
            await validator.validate(f"garbage_{i}")

    assert len(validator._cache) == 10
    assert resolver.calls == 100


@pytest.mark.asyncio
async def test_lru_evicts_oldest_first() -> None:
    """The least-recently-used entry is evicted on overflow."""
    resolver = _CountingResolver(resolved=None)
    validator = CachedTokenValidator(resolver=resolver, cache_ttl_seconds=300.0, max_entries=3)

    for tok in ("a", "b", "c"):
        with pytest.raises(TokenValidationError):
            await validator.validate(tok)

    # Touch "a" so it becomes most-recently-used (served from cache, no new call).
    with pytest.raises(TokenValidationError):
        await validator.validate("a")
    assert resolver.calls == 3

    # Insert "d": should evict "b" (now the LRU), not "a".
    with pytest.raises(TokenValidationError):
        await validator.validate("d")

    assert _key("b") not in validator._cache
    assert _key("a") in validator._cache
    assert _key("c") in validator._cache
    assert _key("d") in validator._cache


@pytest.mark.asyncio
async def test_cache_key_is_sha256_digest() -> None:
    """Entries are keyed on the sha256 hexdigest, never the raw token."""
    resolver = _CountingResolver(resolved=None)
    validator = CachedTokenValidator(resolver=resolver, cache_ttl_seconds=300.0)

    raw = "at_some_secret_token"
    with pytest.raises(TokenValidationError):
        await validator.validate(raw)

    assert _key(raw) in validator._cache
    assert raw not in validator._cache


@pytest.mark.asyncio
async def test_positive_caching_preserved() -> None:
    """A valid token resolves once and is served from cache thereafter."""
    resolver = _CountingResolver(resolved=_identity())
    validator = CachedTokenValidator(resolver=resolver, cache_ttl_seconds=300.0)

    first = await validator.validate("at_good")
    second = await validator.validate("at_good")

    assert first.sub == "agnt_x"
    assert second.sub == "agnt_x"
    assert resolver.calls == 1


@pytest.mark.asyncio
async def test_negative_caching_preserved() -> None:
    """A bad token is resolved once; the negative verdict is then cached."""
    resolver = _CountingResolver(resolved=None)
    validator = CachedTokenValidator(resolver=resolver, cache_ttl_seconds=300.0)

    for _ in range(5):
        with pytest.raises(TokenValidationError, match="unknown_token"):
            await validator.validate("at_bad")

    assert resolver.calls == 1


@pytest.mark.asyncio
async def test_ttl_expiry_still_works_under_bound() -> None:
    """TTL behaviour is preserved alongside the LRU bound."""
    resolver = _CountingResolver(resolved=None)
    validator = CachedTokenValidator(resolver=resolver, cache_ttl_seconds=0.01, max_entries=10)

    with pytest.raises(TokenValidationError):
        await validator.validate("at_x")
    time.sleep(0.02)
    with pytest.raises(TokenValidationError):
        await validator.validate("at_x")

    assert resolver.calls == 2


def test_rejects_invalid_max_entries() -> None:
    with pytest.raises(ValueError, match="max_entries"):
        CachedTokenValidator(resolver=_CountingResolver(resolved=None), max_entries=0)


def _key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
