"""Short-TTL, single-flighted cache around direct-binding credential derivation.

The direct-binding twin of the pre-6b ``caching_toolkit_deriver`` (deleted with
the toolkit path): the cross-DB
``derive_credentials`` lookup (admin agent→credential bindings ∩ control
covering credentials) runs on every direct-path request — two DB hits per
request. Bindings change infrequently, so this wraps the authoritative resolver
in a short-TTL LRU keyed on ``(agent_id, vendor, name, version) →
CredentialDerivation``, single-flighted so concurrent misses for one key
collapse to a single Admin+Control lookup.

This is a pure latency optimization layered *over* the authoritative DB lookup —
authorization correctness never depends on the cache, only its staleness is
bounded by the TTL. The LRU is **per instance**; on PostgreSQL the
``InvalidationBus`` drops an agent's entries when its bindings change and the
whole cache when a credential changes, so a binding granted/revoked/suspended
via the binding API reaches every node without waiting for the TTL. Without the
bus (SQLite, a lost LISTEN connection) the TTL bounds staleness, so keep it
short.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass

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
    """

    def __init__(
        self,
        inner: CredentialDeriverProtocol,
        *,
        cache_ttl_seconds: float = DEFAULT_CREDENTIAL_CACHE_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_CACHE_ENTRIES,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._inner = inner
        self._cache_ttl_seconds = cache_ttl_seconds
        self._max_entries = max_entries
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._single_flight: SingleFlight[CredentialDerivation] = SingleFlight()
        # Bumped by every invalidation; see ``derive_credentials``.
        self._generation = 0

    async def derive_credentials(
        self, *, agent_id: str, vendor: str, name: str, version: str
    ) -> CredentialDerivation:
        """Return the agent's derivation for the API, served from cache when fresh."""
        key = self._make_key(agent_id=agent_id, vendor=vendor, name=name, version=version)
        now = time.monotonic()

        cached = self._cache.get(key)
        if cached is not None and (now - cached.cached_at) < self._cache_ttl_seconds:
            self._cache.move_to_end(key)
            _cache_hits.add(1)
            return cached.value

        generation = self._generation

        async def _load() -> CredentialDerivation:
            result = await self._inner.derive_credentials(
                agent_id=agent_id, vendor=vendor, name=name, version=version
            )
            # Skip caching a read that an invalidation overtook: it may
            # predate the change the invalidation announced.
            if generation == self._generation:
                self._store(key, _CacheEntry(value=result, cached_at=time.monotonic()))
            _cache_misses.add(1)
            logger.debug("credential_binding_cache_miss", agent_id=agent_id, vendor=vendor)
            return result

        # Generation in the flight key: a caller arriving after an
        # invalidation never shares a read that began before it.
        return await self._single_flight.do(f"{key}\x00{generation}", _load)

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
        self.invalidate_agents((agent_id,))

    def invalidate_credential(self, credential_id: str) -> None:
        """Evict cached derivations when a credential is deleted or changed (#1837).

        A derivation entry's ``api_served`` and ``identity_mismatch`` fields can
        depend on any covering credential even when ``credential_id`` is not in
        ``entry.value.credentials``, so a credential mutation clears the cache.
        """
        self.clear()

    def clear(self) -> None:
        """Drop all cached entries (useful for tests/operational invalidation)."""
        self._generation += 1
        self._cache.clear()

    def invalidate_agents(self, agent_ids: Iterable[str]) -> None:
        """Drop every cached derivation for these agents (any API)."""
        self._generation += 1
        prefixes = tuple(f"{agent_id}\x00" for agent_id in agent_ids)
        if not prefixes:
            return
        for key in [k for k in self._cache if k.startswith(prefixes)]:
            del self._cache[key]
