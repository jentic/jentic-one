"""Async request-coalescing helper (single-flight).

A plain TTL cache only helps on a *hit*. On a miss — TTL expiry, a hot key on a
cold instance, or startup — N concurrent callers for the same key all miss
together and each launches its own expensive lookup (a *cache stampede*). The
expensive path is then amplified by the exact concurrency the cache was meant
to absorb.

:class:`SingleFlight` collapses that herd: the **first** caller for a key
installs an :class:`asyncio.Future`; concurrent callers await the in-flight
Future instead of launching their own lookup. The single result *or exception*
is delivered to all waiters, and the in-flight entry is removed in a ``finally``
so a failed lookup never pins a poisoned Future (a later caller re-runs it).

Pure and dependency-free so every read-mostly cache coalesces the same way;
:class:`jentic_one.shared.cache.ttl_lru.AsyncTTLCache` builds on it. Single-flight
is **per instance** — it collapses the herd within a node, which is where the
amplification hurts.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Hashable

# Default ceiling on concurrently in-flight keys. The map only holds entries for
# the brief window a lookup is running, so this bounds a pathological burst of
# unique concurrent misses rather than steady-state size.
DEFAULT_MAX_IN_FLIGHT = 10_000


class SingleFlight[V]:
    """Coalesces concurrent lookups for the same key into one execution.

    Generic over the resolved value type ``V``. Bounded by ``max_in_flight`` so a
    flood of unique concurrent keys cannot grow the in-flight map without limit;
    when the map is full a brand-new key falls back to running its lookup without
    coalescing (correctness is preserved — only the stampede guard is skipped).
    """

    def __init__(self, *, max_in_flight: int = DEFAULT_MAX_IN_FLIGHT) -> None:
        if max_in_flight < 1:
            raise ValueError("max_in_flight must be >= 1")
        self._max_in_flight = max_in_flight
        self._in_flight: dict[Hashable, asyncio.Future[V]] = {}

    @property
    def in_flight_count(self) -> int:
        """Number of lookups currently running (for tests/metrics)."""
        return len(self._in_flight)

    async def do(self, key: Hashable, fn: Callable[[], Awaitable[V]]) -> V:
        """Run ``fn`` for ``key`` once, sharing the result with concurrent callers.

        The first caller for an idle ``key`` runs ``fn``; concurrent callers await
        the same in-flight Future and receive the identical result or exception.
        The in-flight entry is always removed before returning so a failed lookup
        leaves nothing pinned.

        Cancellation is per caller. A cancelled waiter stops waiting without
        touching the shared lookup. A cancelled leader aborts the lookup it was
        running (it lives in the leader's task), and the surviving waiters elect
        a new leader that re-runs ``fn`` once for all of them.
        """
        while (existing := self._in_flight.get(key)) is not None:
            # ``asyncio.wait`` never cancels what it waits on, so cancelling this
            # waiter (client disconnect, request timeout) raises here without
            # cancelling the Future every other caller shares.
            await asyncio.wait((existing,))
            if not existing.cancelled():
                return existing.result()
            # The leader was cancelled mid-lookup and its entry is already gone:
            # loop so one surviving waiter takes the lookup over for the rest.

        # Map full and this is a new key: skip coalescing rather than grow
        # unbounded. The lookup still runs correctly, just without herd collapse.
        if len(self._in_flight) >= self._max_in_flight:
            return await fn()

        loop = asyncio.get_running_loop()
        future: asyncio.Future[V] = loop.create_future()
        self._in_flight[key] = future
        try:
            result = await fn()
        except asyncio.CancelledError:
            # The leader's own cancellation aborts only its lookup. Cancelling the
            # shared Future (rather than storing the CancelledError on it) tells
            # the waiters to re-run the lookup instead of failing as cancelled.
            future.cancel()
            raise
        except BaseException as exc:  # propagate to all waiters, pin nothing
            future.set_exception(exc)
            # The leader re-raises; waiters receive `exc` via their own await. We
            # retrieve the exception here so Python doesn't log it as "never
            # retrieved" when no concurrent waiter happens to be attached.
            future.exception()
            raise
        else:
            future.set_result(result)
            return result
        finally:
            # Remove only our own entry — a later generation may have re-installed
            # this key after we finished.
            if self._in_flight.get(key) is future:
                del self._in_flight[key]
