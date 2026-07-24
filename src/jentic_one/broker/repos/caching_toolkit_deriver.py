"""Short-TTL, single-flighted cache around toolkit derivation (§05 R3).

The cross-DB ``derive_toolkits`` lookup (admin agent→toolkit bindings ∩ control
toolkit→credential bindings) runs on **every** request that lacks a
``Jentic-Toolkit-Id`` header — two DB hits per request. Agent/credential
bindings change infrequently, so this wraps the authoritative resolver in a
short-TTL LRU keyed on ``(agent_id, vendor, name, version) → ToolkitDerivation``,
invalidated on TTL only, and single-flighted (§05 R3.1) so concurrent misses for
one key collapse to a single Admin+Control lookup.

This is a pure latency optimization layered *over* the authoritative DB lookup —
authorization correctness never depends on the cache, only its staleness is
bounded by the TTL. In a cluster the LRU is **per instance**, so a binding
granted/revoked via Admin only becomes consistent after the TTL lapses on each
node (acceptable for the read-mostly default; keep the TTL short).

It composes around any :class:`ToolkitDeriverProtocol` without touching the raw
resolver's SQL, and is itself a :class:`ToolkitDeriverProtocol`.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass

import structlog

from jentic_one.broker.core.singleflight import SingleFlight
from jentic_one.shared.broker.protocols import ToolkitDerivation, ToolkitDeriverProtocol
from jentic_one.shared.metrics import get_meter

logger = structlog.get_logger(__name__)
_meter = get_meter("broker")
_cache_hits = _meter.create_counter("broker.toolkit_cache.hits", description="Toolkit cache hits")
_cache_misses = _meter.create_counter(
    "broker.toolkit_cache.misses", description="Toolkit cache misses"
)

DEFAULT_TOOLKIT_CACHE_TTL_SECONDS = 30.0
# Bound the LRU so a high-cardinality spray of distinct (agent, api) tuples can't
# grow the cache without limit. Derivation keys are low-cardinality in practice
# (bounded by active agents times APIs), so this is generous headroom.
DEFAULT_MAX_CACHE_ENTRIES = 10_000


@dataclass(slots=True)
class _CacheEntry:
    """A cached derivation result with its insertion time (monotonic)."""

    value: ToolkitDerivation
    cached_at: float


class CachingToolkitDeriver:
    """TTL-LRU + single-flight wrapper around a :class:`ToolkitDeriverProtocol`.

    Implements ``ToolkitDeriverProtocol`` itself so it drops in wherever the raw
    resolver is used. A hit within ``cache_ttl_seconds`` returns the cached
    result without touching the DB; concurrent misses for one key are coalesced
    into a single underlying ``derive_toolkits`` call.

    The cached :class:`ToolkitDerivation` is a frozen dataclass of tuples, so it
    is returned directly (no defensive copy). Its ``api_served_toolkits`` and
    ``identity_mismatch`` are cached under the same TTL as the toolkit list;
    those drive recovery guidance, never authorization, so bounded staleness is
    acceptable — the same argument that justifies caching the toolkit list.
    """

    def __init__(
        self,
        inner: ToolkitDeriverProtocol,
        *,
        cache_ttl_seconds: float = DEFAULT_TOOLKIT_CACHE_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_CACHE_ENTRIES,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._inner = inner
        self._cache_ttl_seconds = cache_ttl_seconds
        self._max_entries = max_entries
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._single_flight: SingleFlight[ToolkitDerivation] = SingleFlight()

    async def derive_toolkits(
        self, *, agent_id: str, vendor: str, name: str, version: str
    ) -> ToolkitDerivation:
        """Return the agent's derivation for the API, served from cache when fresh."""
        key = self._make_key(agent_id=agent_id, vendor=vendor, name=name, version=version)
        now = time.monotonic()

        cached = self._cache.get(key)
        if cached is not None and (now - cached.cached_at) < self._cache_ttl_seconds:
            self._cache.move_to_end(key)
            _cache_hits.add(1)
            return cached.value

        async def _load() -> ToolkitDerivation:
            result = await self._inner.derive_toolkits(
                agent_id=agent_id, vendor=vendor, name=name, version=version
            )
            self._store(key, _CacheEntry(value=result, cached_at=time.monotonic()))
            _cache_misses.add(1)
            logger.debug("toolkit_cache_miss", agent_id=agent_id, vendor=vendor, name=name)
            return result

        return await self._single_flight.do(key, _load)

    def _store(self, key: str, entry: _CacheEntry) -> None:
        self._cache[key] = entry
        self._cache.move_to_end(key)
        while len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)

    @staticmethod
    def _make_key(*, agent_id: str, vendor: str, name: str, version: str) -> str:
        # NUL-joined so component boundaries are unambiguous (no value contains it).
        return "\x00".join((agent_id, vendor, name, version))

    def clear(self) -> None:
        """Drop all cached entries (useful for tests/operational invalidation)."""
        self._cache.clear()
