"""Bounded, in-process TTL cache with per-key single-flight loading.

Holds a small number of expensive-to-build values (e.g. a projection of an
upstream document fetched over the network) for a short time. The cache is
bounded by entry count (least-recently-used eviction) and every entry expires
``ttl_seconds`` after it was stored. Concurrent misses for the same key are
coalesced: one caller runs the loader while the others wait on a per-key lock
and then read the freshly stored value, so a burst of identical requests costs
one upstream fetch.

Failures are not cached — a loader exception propagates to its caller and the
next caller for that key runs the loader again. ``clear()`` drops every entry and
discards the result of any load already in flight when it is called. ``ttl_seconds <= 0`` or
``max_entries <= 0`` disables caching entirely (every call runs the loader).
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass, field


@dataclass(slots=True)
class _Entry[V]:
    value: V
    expires_at: float


@dataclass(slots=True)
class _KeyLock:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


class AsyncTTLCache[K: Hashable, V]:
    """LRU + TTL cache whose misses are filled by an async loader, one per key."""

    def __init__(
        self,
        *,
        max_entries: int,
        ttl_seconds: float,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._clock = clock if clock is not None else time.monotonic
        self._entries: OrderedDict[K, _Entry[V]] = OrderedDict()
        self._locks: dict[K, _KeyLock] = {}
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
        self._generation += 1

    async def get_or_load(self, key: K, loader: Callable[[], Awaitable[V]]) -> V:
        """Return the live cached value for ``key``, running ``loader`` on a miss."""
        if not self.enabled:
            return await loader()

        hit = self._lookup(key)
        if hit is not None:
            return hit.value

        key_lock = self._locks.get(key)
        if key_lock is None:
            key_lock = _KeyLock()
            self._locks[key] = key_lock
        key_lock.users += 1
        try:
            async with key_lock.lock:
                # Another caller may have filled the entry while this one waited.
                hit = self._lookup(key)
                if hit is not None:
                    return hit.value
                generation = self._generation
                value = await loader()
                if generation == self._generation:
                    self._store(key, value)
                return value
        finally:
            key_lock.users -= 1
            if key_lock.users == 0:
                self._locks.pop(key, None)

    def _lookup(self, key: K) -> _Entry[V] | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if self._clock() >= entry.expires_at:
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return entry

    def _store(self, key: K, value: V) -> None:
        self._entries[key] = _Entry(value=value, expires_at=self._clock() + self._ttl_seconds)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
