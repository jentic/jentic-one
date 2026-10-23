"""Broker token validation and actor-status checks with short-TTL in-process caches."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from jentic_one.shared.auth.errors import TokenValidationError
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import TokenResolverProtocol
from jentic_one.shared.cache import AsyncTTLCache

DEFAULT_CACHE_TTL_SECONDS = 30
# Hard ceiling on cached token verdicts. The key is a fixed-width sha256 digest,
# but without a bound a flood of *unique* garbage tokens would balloon the
# negative cache and OOM the instance — the cache meant to *save* work would
# become the attack vector. LRU eviction keeps the footprint
# bounded regardless of input cardinality.
DEFAULT_MAX_CACHE_ENTRIES = 10_000


@dataclass
class CachedTokenValidator:
    """Validates opaque tokens via a resolver with short-TTL in-process caching.

    Caches both positive and negative results, keyed on a ``sha256`` digest of the
    token (never the attacker-controlled raw string) under a hard ``max_entries``
    LRU bound. Concurrent misses for the same digest are coalesced into one
    resolver lookup, and an invalidation that lands mid-lookup is not undone by
    it (:class:`~jentic_one.shared.cache.AsyncTTLCache`). Entries past their
    ``expires_at`` are treated as inactive regardless of TTL.
    """

    resolver: TokenResolverProtocol
    cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS
    max_entries: int = DEFAULT_MAX_CACHE_ENTRIES
    _cache: AsyncTTLCache[str, Identity | None] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # AsyncTTLCache rejects a non-positive bound.
        self._cache = AsyncTTLCache(
            ttl_seconds=self.cache_ttl_seconds,
            max_entries=self.max_entries,
            metrics_scope="broker",
            metrics_prefix="broker.token_cache",
        )

    async def validate(self, token: str) -> Identity:
        """Resolve and validate a token.

        Raises ``TokenValidationError`` if the token is inactive, expired, or
        unknown — the broker edge maps that to a uniform 401.
        """
        cache_key = hashlib.sha256(token.encode()).hexdigest()
        # A raised lookup reaches every coalesced waiter and is not cached.
        resolved = await self._cache.get_or_load(
            cache_key, lambda: self.resolver.resolve_access_token(token)
        )
        return self._check_active(resolved)

    def _check_active(self, resolved: Identity | None) -> Identity:
        if resolved is None:
            raise TokenValidationError("unknown_token")
        if not resolved.active:
            raise TokenValidationError("token_inactive")
        if resolved.expires_at is not None and resolved.expires_at <= datetime.now(UTC):
            raise TokenValidationError("token_expired")
        return resolved

    def invalidate(self, token: str) -> None:
        """Remove a token from the cache (useful for testing)."""
        self._cache.invalidate(hashlib.sha256(token.encode()).hexdigest())

    def invalidate_actor(self, actor_id: str) -> None:
        """Evict cached token verdicts for ``actor_id`` and any negative entries (#1837).

        Negative entries (``resolved is None``) do not carry an ``actor_id``
        (e.g. an API key resolved while its agent was disabled), so they are
        evicted alongside the actor's positive entries so re-enabling an agent
        takes effect immediately.
        """
        self._cache.invalidate_where(
            lambda _key, resolved: resolved is None or resolved.sub == actor_id
        )

    def clear(self) -> None:
        """Clear the entire cache."""
        self._cache.clear()


class ActorStatusProtocol(Protocol):
    """Answers whether an actor may currently act (``ActorStatusResolver`` satisfies it)."""

    async def is_active(self, *, actor_id: str, actor_type: str) -> bool: ...


@dataclass
class CachedActorStatus:
    """``ActorStatusProtocol`` over a resolver, with a short-TTL LRU verdict cache.

    The broker's self-contained JWT path asks this once per request after the
    signature checks out, so a disabled agent, or an owned agent whose owner is
    disabled or deleted, stops working within ``cache_ttl_seconds``, the same
    bound the opaque path's ``CachedTokenValidator`` gives. Positive and
    negative verdicts are cached under a hard ``max_entries`` bound, and
    concurrent misses for one actor share a single lookup.
    """

    resolver: ActorStatusProtocol
    cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS
    max_entries: int = DEFAULT_MAX_CACHE_ENTRIES
    _cache: AsyncTTLCache[tuple[str, str], bool] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # AsyncTTLCache rejects a non-positive bound.
        self._cache = AsyncTTLCache(
            ttl_seconds=self.cache_ttl_seconds,
            max_entries=self.max_entries,
            metrics_scope="broker",
            metrics_prefix="broker.actor_status_cache",
        )

    async def is_active(self, *, actor_id: str, actor_type: str) -> bool:
        return await self._cache.get_or_load(
            (actor_type, actor_id),
            lambda: self.resolver.is_active(actor_id=actor_id, actor_type=actor_type),
        )

    def invalidate_actor(self, actor_id: str) -> None:
        """Evict cached status verdicts for ``actor_id`` across actor types (#1837)."""
        self._cache.invalidate_where(lambda key, _active: key[1] == actor_id)

    def clear(self) -> None:
        """Clear every cached verdict."""
        self._cache.clear()
