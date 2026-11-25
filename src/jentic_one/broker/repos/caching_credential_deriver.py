"""Short-TTL, single-flighted cache around direct-binding credential derivation.

The direct-binding twin of the pre-6b ``caching_toolkit_deriver`` (deleted with
the toolkit path): the cross-DB
``derive_credentials`` lookup (admin agent→credential bindings ∩ control
covering credentials) runs on every direct-path request — two DB hits per
request. Bindings change infrequently, so this wraps the authoritative resolver
in a short-TTL LRU keyed on ``(agent_id, vendor, name, version) →
CredentialDerivation``, invalidated on TTL only, and single-flighted so
concurrent misses for one key collapse to a single Admin+Control lookup.

This is a pure latency optimization layered *over* the authoritative DB lookup —
authorization correctness never depends on the cache, only its staleness is
bounded by the TTL. In a cluster the LRU is **per instance**, so a binding
granted/revoked/suspended via the binding API only becomes consistent after the
TTL lapses on each node (acceptable for the read-mostly default; keep the TTL
short).
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

from jentic_one.broker.core.singleflight import SingleFlight
from jentic_one.shared.broker.protocols import CredentialDerivation, CredentialDeriverProtocol
from jentic_one.shared.metrics import get_meter

logger = structlog.get_logger(__name__)
_meter = get_meter("broker")
_cache_hits = _meter.create_counter(
    "broker.credential_binding_cache.hits", description="Credential-binding cache hits"
)
_cache_misses = _meter.create_counter(
    "broker.credential_binding_cache.misses", description="Credential-binding cache misses"
)

DEFAULT_CREDENTIAL_CACHE_TTL_SECONDS = 30.0
# Bound the LRU so a high-cardinality spray of distinct (agent, api) tuples
# can't grow the cache without limit. Derivation keys are low-cardinality in
# practice (bounded by active agents times APIs), so this is generous headroom.
DEFAULT_MAX_CACHE_ENTRIES = 10_000


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class _CacheEntry:
    """A cached derivation result with its insertion time (monotonic)."""

    value: CredentialDerivation
    cached_at: float


class CachingCredentialDeriver:
    """TTL-LRU + single-flight wrapper around a :class:`CredentialDeriverProtocol`.

    Implements ``CredentialDeriverProtocol`` itself so it drops in wherever the
    raw resolver is used. A hit within ``cache_ttl_seconds`` returns the cached
    result without touching the DB; concurrent misses for one key are coalesced
    into a single underlying ``derive_credentials`` call.

    The cached :class:`CredentialDerivation` is a frozen dataclass of frozen
    values, so it is returned directly (no defensive copy). Its ``api_served``
    and ``identity_mismatch`` are cached under the same TTL as the candidate
    list; those drive recovery guidance, never authorization, so bounded
    staleness is acceptable — the same argument that justifies caching the
    candidate list.

    An entry whose derivation carries ``valid_until`` is never served at or
    after that moment, even inside the TTL, so a binding's expiry is exact
    rather than bounded by the TTL. ``clock`` (wall-clock, timezone-aware) is
    injectable so tests can cross the boundary without sleeping. A derivation
    that is not ``cacheable`` (a candidate binding counts uses) is never kept.
    """

    def __init__(
        self,
        inner: CredentialDeriverProtocol,
        *,
        cache_ttl_seconds: float = DEFAULT_CREDENTIAL_CACHE_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_CACHE_ENTRIES,
        clock: Callable[[], datetime] = _utc_now,
        candidates_live: Callable[[str, CredentialDerivation], Awaitable[bool]] | None = None,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._inner = inner
        self._cache_ttl_seconds = cache_ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._candidates_live = candidates_live
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._single_flight: SingleFlight[CredentialDerivation] = SingleFlight()

    async def derive_credentials(
        self, *, agent_id: str, vendor: str, name: str, version: str
    ) -> CredentialDerivation:
        """Return the agent's derivation for the API, served from cache when fresh."""
        key = self._make_key(agent_id=agent_id, vendor=vendor, name=name, version=version)
        now = time.monotonic()

        async def reusable(value: CredentialDerivation) -> bool:
            return (
                not value.credentials
                or self._candidates_live is None
                or await self._candidates_live(agent_id, value)
            )

        cached = self._cache.get(key)
        if (
            cached is not None
            and self._is_fresh(cached, now)
            and await reusable(cached.value)
            and self._cache.get(key) is cached
            and self._is_fresh(cached, time.monotonic())
        ):
            self._cache.move_to_end(key)
            _cache_hits.add(1)
            return cached.value

        loaded_here = False

        async def _load() -> CredentialDerivation:
            nonlocal loaded_here
            loaded_here = True
            result = await self._inner.derive_credentials(
                agent_id=agent_id, vendor=vendor, name=name, version=version
            )
            # A derivation through a use-counted binding must reach the counter
            # on every call, so it is shared only with this load's waiters.
            if result.cacheable:
                self._store(key, _CacheEntry(value=result, cached_at=time.monotonic()))
            else:
                self._cache.pop(key, None)
            _cache_misses.add(1)
            logger.debug("credential_binding_cache_miss", agent_id=agent_id, vendor=vendor)
            return result

        while True:
            loaded_here = False
            result = await self._single_flight.do(key, _load)
            # The database read or this waiter's wakeup can straddle a deadline.
            # Re-derive so surviving bindings and the denial are decided together.
            if (loaded_here or await reusable(result)) and (
                result.valid_until is None or self._clock() < result.valid_until
            ):
                return result

    def _is_fresh(self, entry: _CacheEntry, now: float) -> bool:
        """Within the TTL and strictly before the derivation's ``valid_until``."""
        if (now - entry.cached_at) >= self._cache_ttl_seconds:
            return False
        valid_until = entry.value.valid_until
        return valid_until is None or self._clock() < valid_until

    def _store(self, key: str, entry: _CacheEntry) -> None:
        self._cache[key] = entry
        self._cache.move_to_end(key)
        while len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)

    @staticmethod
    def _make_key(*, agent_id: str, vendor: str, name: str, version: str) -> str:
        # NUL-joined so component boundaries are unambiguous (no value contains it).
        return "\x00".join((agent_id, vendor, name, version))

    def invalidate_agent(self, agent_id: str) -> None:
        """Evict all cached derivations for ``agent_id`` (#1837)."""
        prefix = f"{agent_id}\x00"
        stale = [k for k in self._cache if k.startswith(prefix)]
        for k in stale:
            self._cache.pop(k, None)

    def invalidate_credential(self, credential_id: str) -> None:
        """Evict cached derivations when a credential is deleted or changed (#1837).

        A derivation entry's ``api_served`` and ``identity_mismatch`` fields can
        depend on any covering credential even when ``credential_id`` is not in
        ``entry.value.credentials``, so a credential mutation clears the cache.
        """
        self._cache.clear()

    def clear(self) -> None:
        """Drop all cached entries (useful for tests/operational invalidation)."""
        self._cache.clear()
