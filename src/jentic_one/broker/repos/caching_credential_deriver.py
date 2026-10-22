"""Short-TTL, single-flighted cache around direct-binding derivation.

The direct-binding twin of the pre-6b ``caching_toolkit_deriver`` (deleted with
the toolkit path). Two read-mostly lookups are cached, each in its own TTL LRU:

- ``derive_bindings`` (admin agent→credential bindings), keyed on ``agent_id``
  and read on every direct-path request.
- ``diagnose_unserved`` (control-DB denial guidance), keyed on
  ``(agent_id, vendor, name, version)`` and read only when a request is denied
  for lack of a covering credential — an agent retrying a denied call is served
  from cache.

Credential state (active, connected, which API it covers, its header fields)
is **not** cached here: the broker reads it live per request
(``CredentialRepository.list_covering``), so a disabled or re-scoped credential
stops being selectable on the next request whatever this cache holds.

This is a pure latency optimization layered *over* the authoritative DB
lookups, single-flighted so concurrent misses for one key collapse to a single
lookup. In a cluster the LRUs are **per instance**, so a binding
granted/revoked/suspended via the binding API only becomes consistent after the
TTL lapses on each node that misses the invalidation (acceptable for the
read-mostly default; keep the TTL short).
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog

from jentic_one.broker.core.singleflight import SingleFlight
from jentic_one.shared.broker.protocols import (
    AgentBindings,
    CredentialDeriverProtocol,
    UnservedDiagnosis,
)
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
# Bound each LRU so a high-cardinality spray of distinct agents / (agent, api)
# tuples can't grow the cache without limit. Keys are low-cardinality in
# practice (bounded by active agents times APIs), so this is generous headroom.
DEFAULT_MAX_CACHE_ENTRIES = 10_000


@dataclass(slots=True)
class _CacheEntry[T]:
    """A cached value with its insertion time (monotonic)."""

    value: T
    cached_at: float


class _TTLCache[T]:
    """A TTL LRU with single-flighted misses, labelled for its metrics."""

    def __init__(self, *, kind: str, ttl_seconds: float, max_entries: int) -> None:
        self._kind = kind
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._entries: OrderedDict[str, _CacheEntry[T]] = OrderedDict()
        self._single_flight: SingleFlight[T] = SingleFlight()

    async def get(self, key: str, load: Callable[[], Awaitable[T]]) -> T:
        cached = self._entries.get(key)
        if cached is not None and (time.monotonic() - cached.cached_at) < self._ttl_seconds:
            self._entries.move_to_end(key)
            _cache_hits.add(1, {"kind": self._kind})
            return cached.value

        async def _load() -> T:
            value = await load()
            self._entries[key] = _CacheEntry(value=value, cached_at=time.monotonic())
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
            _cache_misses.add(1, {"kind": self._kind})
            return value

        return await self._single_flight.do(key, _load)

    def pop(self, key: str) -> None:
        self._entries.pop(key, None)

    def pop_prefix(self, prefix: str) -> None:
        for k in [k for k in self._entries if k.startswith(prefix)]:
            self._entries.pop(k, None)

    def clear(self) -> None:
        self._entries.clear()


class CachingCredentialDeriver:
    """TTL-LRU + single-flight wrapper around a :class:`CredentialDeriverProtocol`.

    Implements ``CredentialDeriverProtocol`` itself so it drops in wherever the
    raw resolver is used. Cached values are frozen dataclasses of frozen
    values, so they are returned directly (no defensive copy).
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
        self._bindings: _TTLCache[AgentBindings] = _TTLCache(
            kind="bindings", ttl_seconds=cache_ttl_seconds, max_entries=max_entries
        )
        self._diagnoses: _TTLCache[UnservedDiagnosis] = _TTLCache(
            kind="diagnosis", ttl_seconds=cache_ttl_seconds, max_entries=max_entries
        )

    async def derive_bindings(self, *, agent_id: str) -> AgentBindings:
        """Return the agent's bindings, served from cache when fresh."""

        async def _load() -> AgentBindings:
            logger.debug("credential_binding_cache_miss", agent_id=agent_id)
            return await self._inner.derive_bindings(agent_id=agent_id)

        return await self._bindings.get(agent_id, _load)

    async def diagnose_unserved(
        self, *, bindings: AgentBindings, vendor: str, name: str, version: str
    ) -> UnservedDiagnosis:
        """Return the denial diagnosis for the agent and API, served from cache when fresh."""

        async def _load() -> UnservedDiagnosis:
            return await self._inner.diagnose_unserved(
                bindings=bindings, vendor=vendor, name=name, version=version
            )

        # NUL-joined so component boundaries are unambiguous (no value contains it).
        key = "\x00".join((bindings.agent_id, vendor, name, version))
        return await self._diagnoses.get(key, _load)

    def invalidate_agent(self, agent_id: str) -> None:
        """Evict the agent's cached bindings and diagnoses (#1837)."""
        self._bindings.pop(agent_id)
        self._diagnoses.pop_prefix(f"{agent_id}\x00")

    def invalidate_credential(self, credential_id: str) -> None:
        """Evict what a credential mutation can change (#1837).

        A diagnosis's ``api_served`` and ``scope_miss`` can depend on any
        covering credential, bound to the agent or not, so every diagnosis is
        dropped. Bindings are dropped too: a credential delete cascades to its
        bindings, and that may be signalled by credential id alone.
        """
        self._bindings.clear()
        self._diagnoses.clear()

    def clear(self) -> None:
        """Drop all cached entries (useful for tests/operational invalidation)."""
        self._bindings.clear()
        self._diagnoses.clear()
