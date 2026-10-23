"""Bounded TTL + LRU caches for in-process, read-mostly lookups.

Two layers:

- :class:`TTLLRU` is the plain storage: an ``OrderedDict`` of entries, each with
  its own monotonic expiry, bounded by ``max_entries`` with least-recently-used
  eviction. It is synchronous and does no locking, so a caller shared across
  threads (``shared.dns_cache``) wraps it in its own lock.
- :class:`AsyncTTLCache` is the read-through cache the broker, admin and
  integration caches use: :meth:`AsyncTTLCache.get_or_load` serves a fresh entry
  or runs the loader once per key for every concurrent caller
  (:class:`~jentic_one.shared.cache.singleflight.SingleFlight`).

Invariants :class:`AsyncTTLCache` holds for every caller:

- **Pre-lookup stamping.** An entry's age is measured from the moment its load
  began, so a value never outlives the TTL measured from when its source read
  started (a slow DB round trip does not extend it).
- **Invalidations stick.** Every invalidation bumps a generation counter. A load
  that began under an older generation hands its result to the callers already
  waiting on it but never stores it, and a caller arriving after the
  invalidation starts a fresh load instead of joining the stale one. Without
  this, an ``invalidate_*`` that lands while a load is in flight is undone when
  the load stores its pre-invalidation result for a full TTL. The counter is
  per cache, not per key, so an invalidation also discards an unrelated load in
  flight: a load too many is cheap next to a stale verdict surviving a
  revocation.
- **Failures are not cached.** A raised loader reaches every waiter and leaves
  nothing stored; the next caller retries.
- A ``ttl_seconds`` of ``0`` (or a ``ttl_for`` that returns ``<= 0``) disables
  storage for that value while keeping the single-flight coalescing.

Metrics: a cache reports to ``{prefix}.hits`` / ``.misses`` / ``.evictions`` on
the meter its owner names (``metrics_scope``), so a cache that moves onto this
primitive keeps emitting its counters under the instrumentation scope they were
created in. ``misses`` counts **every caller that does not find a fresh entry**,
including the waiters a single-flight load coalesces and the callers a failed
load raises to, so ``hits + misses`` is the number of ``get_or_load`` calls.
"""

from __future__ import annotations

import math
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable, Iterator
from dataclasses import dataclass

from opentelemetry.metrics import Counter

from jentic_one.shared.cache.singleflight import DEFAULT_MAX_IN_FLIGHT, SingleFlight
from jentic_one.shared.metrics import get_meter

__all__ = ["TTLLRU", "AsyncTTLCache", "CacheStats"]

# One counter triple per (meter name, metric prefix), shared by every cache
# instance that reports under it (OpenTelemetry instruments are process-wide by
# name). The meter name is the owner's area ("admin", "broker", …), which is the
# instrumentation scope the exporter labels the series with, so it stays with
# the area that created the counters rather than with this module.
_counters: dict[tuple[str, str], tuple[Counter, Counter, Counter]] = {}


def _counters_for(scope: str, prefix: str) -> tuple[Counter, Counter, Counter]:
    counters = _counters.get((scope, prefix))
    if counters is None:
        meter = get_meter(scope)
        counters = (
            meter.create_counter(f"{prefix}.hits", description=f"{prefix} hits"),
            meter.create_counter(f"{prefix}.misses", description=f"{prefix} misses"),
            meter.create_counter(f"{prefix}.evictions", description=f"{prefix} LRU evictions"),
        )
        _counters[(scope, prefix)] = counters
    return counters


@dataclass(slots=True)
class _Entry[V]:
    value: V
    expires_at: float


class TTLLRU[K: Hashable, V]:
    """Size-bounded map whose entries expire at a per-entry monotonic deadline.

    Not thread-safe: callers shared across threads hold their own lock. Reads
    take ``now`` from the injected ``clock`` unless the caller passes one, so a
    caller can stamp several operations with a single clock read.
    """

    def __init__(
        self,
        *,
        max_entries: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[K, _Entry[V]] = OrderedDict()
        self.evictions = 0

    @property
    def max_entries(self) -> int:
        return self._max_entries

    def get(self, key: K, *, now: float | None = None) -> _Entry[V] | None:
        """Return the live entry for ``key`` and mark it most recently used.

        An expired entry is dropped and reported as absent.
        """
        entry = self._entries.get(key)
        if entry is None:
            return None
        if (self._clock() if now is None else now) >= entry.expires_at:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return entry

    def put(self, key: K, value: V, *, expires_at: float) -> int:
        """Insert or refresh ``key``; return how many LRU entries were evicted."""
        self._entries[key] = _Entry(value=value, expires_at=expires_at)
        self._entries.move_to_end(key)
        evicted = 0
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
            evicted += 1
        self.evictions += evicted
        return evicted

    def pop(self, key: K) -> bool:
        """Remove ``key``; return whether it was present."""
        return self._entries.pop(key, None) is not None

    def remove_where(self, predicate: Callable[[K, V], bool]) -> int:
        """Remove every entry ``predicate(key, value)`` selects; return the count."""
        stale = [k for k, e in self._entries.items() if predicate(k, e.value)]
        for k in stale:
            del self._entries[k]
        return len(stale)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: object) -> bool:
        return key in self._entries

    def __iter__(self) -> Iterator[K]:
        return iter(list(self._entries))


@dataclass(frozen=True, slots=True)
class CacheStats:
    """Per-instance counters (the same events the OpenTelemetry counters record)."""

    hits: int
    misses: int
    evictions: int


class AsyncTTLCache[K: Hashable, V]:
    """Read-through TTL + LRU cache with single-flight loads and sticky invalidation.

    ``ttl_seconds`` is the lifetime of a stored value, measured from when its
    load began; ``ttl_for`` replaces it with a per-value lifetime (``math.inf``
    never expires, ``<= 0`` skips storing), and reading it from live config on
    every store keeps the expiry and the owner's own TTL gate in agreement.
    Exactly one of the two is required.

    ``metrics_prefix`` names the ``{prefix}.hits`` / ``.misses`` / ``.evictions``
    counters this instance reports to and ``metrics_scope`` the meter (the
    owner's area) they are created on; instances sharing both share the
    counters. Both or neither.

    Not thread-safe: one instance belongs to one event loop.

    :meth:`put`, :meth:`get`, :meth:`keys` and :attr:`generation` are the seam
    this primitive's own tests and its callers' tests drive it through; the
    production paths use :meth:`get_or_load` and the ``invalidate_*`` family.
    """

    def __init__(
        self,
        *,
        max_entries: int,
        ttl_seconds: float | None = None,
        ttl_for: Callable[[V], float] | None = None,
        metrics_scope: str | None = None,
        metrics_prefix: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        max_in_flight: int = DEFAULT_MAX_IN_FLIGHT,
    ) -> None:
        if (ttl_seconds is None) == (ttl_for is None):
            raise ValueError("pass exactly one of ttl_seconds or ttl_for")
        if (metrics_scope is None) != (metrics_prefix is None):
            raise ValueError("metrics_scope and metrics_prefix go together")
        if ttl_for is not None:
            self._ttl_for: Callable[[V], float] = ttl_for
        else:
            fixed = 0.0 if ttl_seconds is None else ttl_seconds
            self._ttl_for = lambda _value: fixed
        self._clock = clock
        self._store = TTLLRU[K, V](max_entries=max_entries, clock=clock)
        self._flight: SingleFlight[V] = SingleFlight(max_in_flight=max_in_flight)
        self._generation = 0
        self._hits = 0
        self._misses = 0
        self._otel = (
            _counters_for(metrics_scope, metrics_prefix)
            if metrics_scope is not None and metrics_prefix is not None
            else None
        )

    @property
    def generation(self) -> int:
        """Bumped by every invalidation; a load stores only under its starting value."""
        return self._generation

    @property
    def stats(self) -> CacheStats:
        return CacheStats(hits=self._hits, misses=self._misses, evictions=self._store.evictions)

    def get(self, key: K) -> V | None:
        """Return the fresh cached value for ``key`` without loading (``None`` on a miss)."""
        entry = self._store.get(key)
        return entry.value if entry is not None else None

    async def get_or_load(self, key: K, loader: Callable[[], Awaitable[V]]) -> V:
        """Return the fresh value for ``key``, running ``loader`` on a miss.

        Concurrent misses for ``key`` within one generation share a single
        ``loader`` call and its result or exception.
        """
        entry = self._store.get(key)
        if entry is not None:
            self._record_hit()
            return entry.value
        self._record_miss()

        generation = self._generation

        async def _load() -> V:
            started = self._clock()
            value = await loader()
            if generation == self._generation:
                self._put(key, value, started)
            return value

        # The generation is part of the flight key: a caller arriving after an
        # invalidation never joins a load that began before it.
        return await self._flight.do((generation, key), _load)

    def put(self, key: K, value: V, *, stamped_at: float | None = None) -> None:
        """Store ``value`` for ``key`` outside :meth:`get_or_load`.

        Its age counts from ``stamped_at`` (a ``clock`` reading; defaults to now).
        """
        self._put(key, value, self._clock() if stamped_at is None else stamped_at)

    def _put(self, key: K, value: V, started: float) -> None:
        ttl = self._ttl_for(value)
        if ttl <= 0:
            return
        expires_at = math.inf if math.isinf(ttl) else started + ttl
        evicted = self._store.put(key, value, expires_at=expires_at)
        if evicted and self._otel is not None:
            self._otel[2].add(evicted)

    def invalidate(self, key: K) -> bool:
        """Evict ``key`` and fence off loads already in flight; return whether it was cached."""
        self._generation += 1
        return self._store.pop(key)

    def invalidate_prefix(self, prefix: str) -> int:
        """Evict every ``str`` key starting with ``prefix``; return the count."""
        self._generation += 1
        return self._store.remove_where(lambda k, _v: isinstance(k, str) and k.startswith(prefix))

    def invalidate_where(self, predicate: Callable[[K, V], bool]) -> int:
        """Evict every entry ``predicate(key, value)`` selects; return the count."""
        self._generation += 1
        return self._store.remove_where(predicate)

    def clear(self) -> None:
        """Evict everything and fence off loads already in flight."""
        self._generation += 1
        self._store.clear()

    def keys(self) -> list[K]:
        """Snapshot of the stored keys, least recently used first (may include expired)."""
        return list(self._store)

    def __len__(self) -> int:
        return len(self._store)

    def __contains__(self, key: object) -> bool:
        return key in self._store

    def _record_hit(self) -> None:
        self._hits += 1
        if self._otel is not None:
            self._otel[0].add(1)

    def _record_miss(self) -> None:
        self._misses += 1
        if self._otel is not None:
            self._otel[1].add(1)
