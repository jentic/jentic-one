"""Async request-coalescing helper (single-flight).

A plain TTL cache only helps on a *hit*. On a miss — TTL expiry, a hot key on a
cold instance, or startup — N concurrent callers for the same key all miss
together and each launches its own expensive lookup (a *cache stampede*). The
expensive path is then amplified by the exact concurrency the cache was meant
to absorb.

:class:`SingleFlight` collapses that herd: the **first** caller for a key starts
one :class:`asyncio.Task` for the lookup and every caller — the first included —
awaits it through :func:`asyncio.shield`. The single result *or exception* is
delivered to all waiters, and the in-flight entry is removed in a ``finally`` so
a failed lookup never pins a poisoned task (a later caller re-runs it).

Running the lookup in its own task is what makes cancellation *per caller*:
a caller that goes away (client disconnect, request timeout) stops waiting
without disturbing the lookup the other callers are awaiting. The lookup is
cancelled only when its **last** waiter goes, so nothing keeps running
unobserved. Callers on a request path (the registry's catalog preview) depend on
this: one cancelled request must not make the others refetch.

Pure and dependency-free so every read-mostly cache coalesces the same way;
:class:`jentic_one.shared.cache.ttl_lru.AsyncTTLCache` builds on it. Single-flight
is **per instance** — it collapses the herd within a node, which is where the
amplification hurts.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass

# Default ceiling on concurrently in-flight keys. The map only holds entries for
# the brief window a lookup is running, so this bounds a pathological burst of
# unique concurrent misses rather than steady-state size.
DEFAULT_MAX_IN_FLIGHT = 10_000


@dataclass(slots=True)
class _Flight[V]:
    """One running lookup and how many callers are currently awaiting it."""

    task: asyncio.Task[V]
    waiters: int = 0


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
        self._in_flight: dict[Hashable, _Flight[V]] = {}

    @property
    def in_flight_count(self) -> int:
        """Number of lookups currently running (for tests/metrics)."""
        return len(self._in_flight)

    async def do(self, key: Hashable, fn: Callable[[], Awaitable[V]]) -> V:
        """Run ``fn`` for ``key`` once, sharing the result with concurrent callers.

        The first caller for an idle ``key`` starts the lookup in its own task;
        concurrent callers await that same task and receive the identical result
        or exception. The in-flight entry is always removed before the lookup
        returns, so a failed lookup leaves nothing pinned.

        Cancellation is per caller: a cancelled caller stops waiting without
        cancelling the lookup the others share. Cancelling the **last** waiter
        cancels the lookup rather than leaving it running unobserved, and a
        caller arriving afterwards starts a fresh one. A lookup that runs
        uncoalesced (``max_in_flight`` reached) runs in its caller's task and so
        dies with that caller.
        """
        flight = self._in_flight.get(key)
        if flight is None:
            # Map full and this is a new key: skip coalescing rather than grow
            # unbounded. The lookup still runs correctly, just without herd
            # collapse.
            if len(self._in_flight) >= self._max_in_flight:
                return await fn()
            flight = _Flight(task=asyncio.create_task(self._run(key, fn)))
            self._in_flight[key] = flight

        flight.waiters += 1
        try:
            return await asyncio.shield(flight.task)
        except asyncio.CancelledError:
            if flight.task.done():
                if not flight.task.cancelled():
                    # Retrieve the result/exception so asyncio does not log it as
                    # "never retrieved" when this was the only caller left.
                    flight.task.exception()
            elif flight.waiters == 1:
                # Last waiter gone: nobody wants this result, so stop the lookup
                # rather than leave it running unobserved.
                if self._in_flight.get(key) is flight:
                    del self._in_flight[key]
                flight.task.cancel()
            raise
        finally:
            flight.waiters -= 1

    async def _run(self, key: Hashable, fn: Callable[[], Awaitable[V]]) -> V:
        try:
            return await fn()
        finally:
            # Release the slot only if it is still ours: a later generation may
            # have re-installed this key after we were detached.
            flight = self._in_flight.get(key)
            if flight is not None and flight.task is asyncio.current_task():
                del self._in_flight[key]
