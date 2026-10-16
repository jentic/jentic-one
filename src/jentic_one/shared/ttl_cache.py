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
``failure_ttl_seconds > 0`` the failure is also remembered for that long, so a
persistently failing key is not reloaded on every call; with the default ``0``
the next call after a failure runs the loader again. Only the failure's type
and scalar args are remembered (never the exception object, whose traceback
and cause chain would keep the loader's locals alive), and each hit raises a
fresh instance with no ``__cause__``. ``clear()`` drops every
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

#: Exception ``args`` of these types are kept verbatim in a remembered failure;
#: anything else is replaced by the exception's message (at most
#: :data:`_MAX_FALLBACK_MESSAGE` characters) so a remembered failure never
#: references objects the loader built (bodies, responses, buffers).
_SCALAR_ARG_TYPES = (str, int, float, bool, type(None))
_MAX_FALLBACK_MESSAGE = 1024


@dataclass(frozen=True, slots=True)
class _Failure:
    """A loader failure reduced to its type and scalar args.

    The live exception carries ``__traceback__``/``__cause__``/``__context__``,
    whose frames keep every loader local (e.g. a capped response body) alive;
    storing it would pin that memory for the whole failure TTL. Only the type
    and plain-value args are kept, and each hit raises a fresh instance.
    """

    exc_type: type[Exception]
    args: tuple[object, ...]

    @classmethod
    def detach(cls, exc: Exception) -> _Failure:
        if all(isinstance(arg, _SCALAR_ARG_TYPES) for arg in exc.args):
            return cls(type(exc), tuple(exc.args))
        return cls(type(exc), (str(exc)[:_MAX_FALLBACK_MESSAGE],))

    def rebuild(self) -> Exception:
        """A fresh, unchained instance of the remembered failure."""
        try:
            return self.exc_type(*self.args)
        except Exception:
            # A type whose constructor rejects its own args still reports as a
            # failure, just not as its original type.
            return RuntimeError(*self.args)


@dataclass(slots=True)
class _Entry[V]:
    value: V | None
    error: _Failure | None
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
                raise hit.error.rebuild()
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
                failure = _Failure.detach(exc)
                self._store(key, _Entry(None, failure, self._clock() + self._failure_ttl_seconds))
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
