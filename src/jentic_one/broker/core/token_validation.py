"""Broker token validation with short-TTL in-process cache."""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from jentic_one.broker.core.singleflight import SingleFlight
from jentic_one.shared.auth.errors import TokenValidationError
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import TokenResolverProtocol

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
    # Bumped by every invalidation; a load only caches its result when no
    # invalidation happened while it was reading.
    _generation: int = field(default=0, init=False, repr=False)

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
            generation = self._generation
            resolved = await self.resolver.resolve_access_token(token)
            # An invalidation that lands while the read is in flight may
            # describe a commit the read did not see: answer this caller, but
            # never cache a verdict that could predate the change.
            if generation == self._generation:
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
        self.invalidate_digests((cache_key,))

    def invalidate_digests(self, digests: Iterable[str]) -> None:
        """Drop the entries for these sha256 token digests.

        The cache key is the hex ``sha256`` of the token — the same digest the
        admin DB stores (``access_tokens.token_hash``,
        ``agent_credentials.api_key_hash``) — so a change to one token row maps
        to exactly one entry.
        """
        self._generation += 1
        for digest in digests:
            self._cache.pop(digest, None)

    def invalidate_matching(self, predicate: Callable[[Identity], bool]) -> None:
        """Drop every entry whose identity matches ``predicate``, and every negative one.

        For changes keyed on something other than the token (the actor, its
        grants, its OAuth client or consent grant). Negative entries go too:
        they carry no identity to match, and an actor change (an agent
        re-enabled, a key re-bound) can turn a token that resolved to nothing
        into a live one. A linear scan over a bounded cache, run only on admin
        writes.
        """
        self._generation += 1
        stale = [
            key
            for key, entry in self._cache.items()
            if entry.resolved is None or predicate(entry.resolved)
        ]
        for key in stale:
            del self._cache[key]

    def clear(self) -> None:
        """Clear the entire cache."""
        self._generation += 1
        self._cache.clear()
