"""Broker token validation and actor-status checks with short-TTL in-process caches."""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from jentic_one.broker.core.singleflight import SingleFlight
from jentic_one.shared.auth.cli_invocation import CliInvocationIdentity
from jentic_one.shared.auth.errors import TokenValidationError
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import (
    CliInvocationResolverProtocol,
    TokenResolverProtocol,
)

DEFAULT_CACHE_TTL_SECONDS = 30
# Hard ceiling on cached token verdicts. The key is a fixed-width sha256 digest,
# but without a bound a flood of *unique* garbage tokens would balloon the
# negative cache and OOM the instance — the cache meant to *save* work would
# become the attack vector. LRU eviction keeps the footprint
# bounded regardless of input cardinality.
DEFAULT_MAX_CACHE_ENTRIES = 10_000


@dataclass(slots=True)
class _CacheEntry:
    """A cached token resolution result (positive or negative)."""

    resolved: Identity | None
    cached_at: float


@dataclass
class CachedTokenValidator:
    """Validates opaque tokens via a resolver with short-TTL in-process caching.

    Caches both positive and negative results, keyed on a ``sha256`` digest of the
    token (never the attacker-controlled raw string) under a hard ``max_entries``
    LRU bound. Concurrent misses for the same digest are coalesced into one
    resolver lookup (:class:`SingleFlight`). Entries past their ``expires_at`` are
    treated as inactive regardless of TTL.
    """

    resolver: TokenResolverProtocol
    cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS
    max_entries: int = DEFAULT_MAX_CACHE_ENTRIES
    _cache: OrderedDict[str, _CacheEntry] = field(
        default_factory=OrderedDict, init=False, repr=False
    )
    _single_flight: SingleFlight[Identity | None] = field(
        default_factory=SingleFlight, init=False, repr=False
    )

    def __post_init__(self) -> None:
        if self.max_entries < 1:
            raise ValueError("max_entries must be >= 1")

    async def validate(self, token: str) -> Identity:
        """Resolve and validate a token.

        Raises ``TokenValidationError`` if the token is inactive, expired, or
        unknown — the broker edge maps that to a uniform 401.
        """
        cache_key = hashlib.sha256(token.encode()).hexdigest()
        now = time.monotonic()

        entry = self._cache.get(cache_key)
        if entry is not None and (now - entry.cached_at) < self.cache_ttl_seconds:
            # Mark as most-recently-used so hot tokens survive eviction.
            self._cache.move_to_end(cache_key)
            return self._check_active(entry.resolved)

        async def _load() -> Identity | None:
            # Stamped with the leader's pre-lookup time, so a verdict never
            # outlives the TTL measured from when its DB read began.
            started = time.monotonic()
            resolved = await self.resolver.resolve_access_token(token)
            self._store(cache_key, _CacheEntry(resolved=resolved, cached_at=started))
            return resolved

        # Concurrent misses for one token (cold start, TTL expiry) share a single
        # resolver lookup; a raised lookup reaches every waiter and is not cached.
        resolved = await self._single_flight.do(cache_key, _load)
        return self._check_active(resolved)

    def _store(self, cache_key: str, entry: _CacheEntry) -> None:
        """Insert/refresh an entry, evicting the LRU entry past ``max_entries``."""
        self._cache[cache_key] = entry
        self._cache.move_to_end(cache_key)
        while len(self._cache) > self.max_entries:
            self._cache.popitem(last=False)

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
        cache_key = hashlib.sha256(token.encode()).hexdigest()
        self._cache.pop(cache_key, None)

    def invalidate_actor(self, actor_id: str) -> None:
        """Evict cached token verdicts for ``actor_id`` and any negative entries (#1837).

        Negative entries (``resolved is None``) do not carry an ``actor_id``
        (e.g. an API key resolved while its agent was disabled), so they are
        evicted alongside the actor's positive entries so re-enabling an agent
        takes effect immediately.
        """
        stale = [
            key
            for key, entry in self._cache.items()
            if entry.resolved is None or entry.resolved.sub == actor_id
        ]
        for key in stale:
            self._cache.pop(key, None)

    def clear(self) -> None:
        """Clear the entire cache."""
        self._cache.clear()


@dataclass(slots=True)
class _CliCacheEntry:
    """A cached invocation-token resolution result (positive or negative)."""

    resolved: CliInvocationIdentity | None
    cached_at: float


@dataclass
class CachedCliInvocationValidator:
    """Validates ``cit_`` CLI invocation tokens with the same short-TTL cache as access tokens.

    Kept apart from :class:`CachedTokenValidator` (and the composite validator
    that wraps it) on purpose: only the broker's CLI-facade dependency calls
    this, so ``/execute`` never accepts an invocation token. Same posture
    otherwise: positive and negative verdicts cached under a ``sha256`` key
    and a hard LRU bound, concurrent misses coalesced, and a cached entry past
    its expiry treated as inactive. Revoking the token or its parent takes
    effect within ``cache_ttl_seconds``.
    """

    resolver: CliInvocationResolverProtocol
    cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS
    max_entries: int = DEFAULT_MAX_CACHE_ENTRIES
    _cache: OrderedDict[str, _CliCacheEntry] = field(
        default_factory=OrderedDict, init=False, repr=False
    )
    _single_flight: SingleFlight[CliInvocationIdentity | None] = field(
        default_factory=SingleFlight, init=False, repr=False
    )

    def __post_init__(self) -> None:
        if self.max_entries < 1:
            raise ValueError("max_entries must be >= 1")

    async def validate(self, token: str) -> CliInvocationIdentity:
        """Resolve and validate an invocation token.

        Raises ``TokenValidationError`` if the token is unknown, revoked,
        expired, or its parent access token no longer resolves as active.
        """
        cache_key = hashlib.sha256(token.encode()).hexdigest()
        entry = self._cache.get(cache_key)
        if entry is not None and (time.monotonic() - entry.cached_at) < self.cache_ttl_seconds:
            self._cache.move_to_end(cache_key)
            return self._check_active(entry.resolved)

        async def _load() -> CliInvocationIdentity | None:
            started = time.monotonic()
            resolved = await self.resolver.resolve_cli_invocation_token(token)
            self._cache[cache_key] = _CliCacheEntry(resolved=resolved, cached_at=started)
            self._cache.move_to_end(cache_key)
            while len(self._cache) > self.max_entries:
                self._cache.popitem(last=False)
            return resolved

        resolved = await self._single_flight.do(cache_key, _load)
        return self._check_active(resolved)

    @staticmethod
    def _check_active(resolved: CliInvocationIdentity | None) -> CliInvocationIdentity:
        if resolved is None:
            raise TokenValidationError("unknown_token")
        identity = resolved.identity
        if not identity.active:
            raise TokenValidationError("token_inactive")
        if identity.expires_at is not None and identity.expires_at <= datetime.now(UTC):
            raise TokenValidationError("token_expired")
        return resolved

    def clear(self) -> None:
        """Clear the entire cache."""
        self._cache.clear()


class ActorStatusProtocol(Protocol):
    """Answers whether an actor may currently act (``ActorStatusResolver`` satisfies it)."""

    async def is_active(self, *, actor_id: str, actor_type: str) -> bool: ...


@dataclass(slots=True)
class _StatusEntry:
    active: bool
    cached_at: float


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
    _cache: OrderedDict[tuple[str, str], _StatusEntry] = field(
        default_factory=OrderedDict, init=False, repr=False
    )
    _single_flight: SingleFlight[bool] = field(default_factory=SingleFlight, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.max_entries < 1:
            raise ValueError("max_entries must be >= 1")

    async def is_active(self, *, actor_id: str, actor_type: str) -> bool:
        key = (actor_type, actor_id)
        entry = self._cache.get(key)
        if entry is not None and (time.monotonic() - entry.cached_at) < self.cache_ttl_seconds:
            self._cache.move_to_end(key)
            return entry.active

        async def _load() -> bool:
            started = time.monotonic()
            active = await self.resolver.is_active(actor_id=actor_id, actor_type=actor_type)
            self._cache[key] = _StatusEntry(active=active, cached_at=started)
            self._cache.move_to_end(key)
            while len(self._cache) > self.max_entries:
                self._cache.popitem(last=False)
            return active

        return await self._single_flight.do(f"{actor_type}:{actor_id}", _load)

    def invalidate_actor(self, actor_id: str) -> None:
        """Evict cached status verdicts for ``actor_id`` across actor types (#1837)."""
        stale = [key for key in self._cache if key[1] == actor_id]
        for key in stale:
            self._cache.pop(key, None)

    def clear(self) -> None:
        """Clear every cached verdict."""
        self._cache.clear()
