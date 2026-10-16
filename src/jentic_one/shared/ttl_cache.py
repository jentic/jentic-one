"""Bounded, in-process TTL cache with per-key single-flight loading.

Holds a small number of expensive-to-build values (e.g. a projection of an
upstream document fetched over the network) for a short time. The cache is
bounded by entry count (least-recently-used eviction) and every entry expires
``ttl_seconds`` after it was stored. Concurrent misses for the same key are
coalesced: the first caller starts one load task and every caller (the first
included) awaits it through :func:`asyncio.shield`, so a burst of identical
requests costs one upstream fetch and every waiter gets the same result — or
the same exception. Cancelling one waiter never cancels the load for the
others; the load is cancelled only once its last waiter has gone.

A loader exception propagates to every waiter of that load. With
``failure_ttl_seconds > 0`` it is also remembered for that long, so a
persistently failing key is not reloaded on every call; with the default ``0``
the next call after a failure runs the loader again. ``clear()`` drops every
entry and detaches loads already in flight: their waiters still get the
result, but it is not stored and later callers start a fresh load.
``ttl_seconds <= 0`` or ``max_entries <= 0`` disables caching entirely (every
call runs the loader).
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass
from typing import cast


@dataclass(slots=True)
class _Entry[V]:
    value: V | None
    error: Exception | None
    expires_at: float


@dataclass(slots=True)
class _Flight[V]:
    task: asyncio.Task[V]
    waiters: int = 0


class AsyncTTLCache[K: Hashable, V]:
    """LRU + TTL cache whose misses are filled by an async loader, one per key."""

    def __init__(
        self,
        *,
        max_entries: int,
        ttl_seconds: float,
        failure_ttl_seconds: float = 0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._failure_ttl_seconds = failure_ttl_seconds
        self._clock = clock if clock is not None else time.monotonic
        self._entries: OrderedDict[K, _Entry[V]] = OrderedDict()
        self._inflight: dict[K, _Flight[V]] = {}
        # Bumped by clear(); a load that started before a clear must not store
        # its (pre-clear) result afterwards.
        self._generation = 0

    @property
    def enabled(self) -> bool:
        """Whether values are cached at all (both bounds must be positive)."""
        return self._max_entries > 0 and self._ttl_seconds > 0

    def __len__(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        """Drop every cached value; loads already in flight will not be stored."""
        self._entries.clear()
        self._inflight.clear()
        self._generation += 1

    async def get_or_load(self, key: K, loader: Callable[[], Awaitable[V]]) -> V:
        """Return the live cached value for ``key``, running ``loader`` on a miss."""
        if not self.enabled:
            return await loader()

        hit = self._lookup(key)
        if hit is not None:
            if hit.error is not None:
                # The remembered exception is re-raised on every hit; dropping
                # its traceback keeps repeated raises from growing it.
                raise hit.error.with_traceback(None)
            return cast("V", hit.value)

        flight = self._inflight.get(key)
        if flight is None:
            flight = _Flight(task=asyncio.create_task(self._load(key, loader, self._generation)))
            self._inflight[key] = flight
        flight.waiters += 1
        try:
            return await asyncio.shield(flight.task)
        except asyncio.CancelledError:
            if flight.waiters == 1 and not flight.task.done():
                # Last waiter gone: nobody wants this result, so stop the load
                # rather than leave it running unobserved.
                if self._inflight.get(key) is flight:
                    del self._inflight[key]
                flight.task.cancel()
            raise
        finally:
            flight.waiters -= 1

    async def _load(self, key: K, loader: Callable[[], Awaitable[V]], generation: int) -> V:
        try:
            value = await loader()
        except Exception as exc:
            if generation == self._generation and self._failure_ttl_seconds > 0:
                self._store(key, _Entry(None, exc, self._clock() + self._failure_ttl_seconds))
            raise
        else:
            if generation == self._generation:
                self._store(key, _Entry(value, None, self._clock() + self._ttl_seconds))
            return value
        finally:
            # The entry is stored before the in-flight slot is released, so a
            # caller arriving in between finds one or the other, never neither.
            flight = self._inflight.get(key)
            if flight is not None and flight.task is asyncio.current_task():
                del self._inflight[key]

    def _lookup(self, key: K) -> _Entry[V] | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if self._clock() >= entry.expires_at:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return entry

    def _store(self, key: K, entry: _Entry[V]) -> None:
        self._entries[key] = entry
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
