"""Broker token validation with short-TTL in-process cache."""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from jentic_one.shared.auth.errors import TokenValidationError
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.broker.protocols import TokenResolverProtocol

DEFAULT_CACHE_TTL_SECONDS = 30
# Hard ceiling on cached token verdicts. The key is a fixed-width sha256 digest,
# but without a bound a flood of *unique* garbage tokens would balloon the
# negative cache and OOM the instance — the cache meant to *save* work would
# become the attack vector (§05 R3/R2.1). LRU eviction keeps the footprint
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
    LRU bound. Entries past their ``expires_at`` are treated as inactive regardless
    of TTL.
    """

    resolver: TokenResolverProtocol
    cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS
    max_entries: int = DEFAULT_MAX_CACHE_ENTRIES
    _cache: OrderedDict[str, _CacheEntry] = field(
        default_factory=OrderedDict, init=False, repr=False
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

        resolved = await self.resolver.resolve_access_token(token)
        self._store(cache_key, _CacheEntry(resolved=resolved, cached_at=now))
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

    def clear(self) -> None:
        """Clear the entire cache."""
        self._cache.clear()
