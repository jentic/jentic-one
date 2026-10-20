"""Per-source fetch limits: an in-process concurrency gate and an optional rate bucket.

Every request a source makes (listing pages and spec fetches alike) holds one
``slot`` for its lane:

- **Concurrency.** At most ``fetch_concurrency`` requests per source are in
  flight in this process. ``interactive_share`` of the slots is kept for the
  ``interactive`` lane (preview, import): ``background`` requests
  (listings, the update sweep, bulk consumers) never take those, while interactive
  requests may also use background capacity. A source with one slot cannot
  reserve any, so both lanes share it.
- **Rate (optional).** With ``requests_per_second`` set, each request also takes
  a token from a bucket of capacity ``burst`` refilled at that rate. On Postgres
  the bucket is a ``catalog_source_buckets`` row, so the rate holds across every
  process sharing the database; on SQLite (one process) it lives in memory.
  ``interactive_share`` of the burst is kept for the interactive lane the same
  way.
- **Throttle.** A ``429`` from the source blocks both lanes until its
  ``Retry-After`` (or a default) has passed: in the bucket row when there is
  one, in process memory otherwise.

A request that would have to wait longer than ``max_wait_s`` for a token, or
that arrives while the source is blocked, fails fast with
``SourceUnavailableError`` carrying the remaining wait, rather than holding a
caller (or a request handler) for it.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal, Protocol

from jentic_one.registry.services.catalog.sources import metrics
from jentic_one.registry.services.catalog.sources.protocol import SourceUnavailableError

FetchLane = Literal["interactive", "background"]

#: How long a ``429`` without a usable ``Retry-After`` blocks the source.
DEFAULT_THROTTLE_SECONDS = 5.0


def background_slots(concurrency: int, interactive_share: float) -> int:
    """How many of ``concurrency`` slots the background lane may hold at once."""
    if concurrency <= 1:
        return concurrency
    reserved = math.ceil(concurrency * interactive_share)
    return max(1, concurrency - reserved)


def background_reserve(burst: int, interactive_share: float) -> float:
    """How many of ``burst`` tokens the background lane must leave for interactive use.

    Capped at ``burst - 1`` (nothing for a one-token bucket), the same way
    :func:`background_slots` always leaves the background lane one slot: a full
    bucket must still grant a background request, or listings would never run.
    """
    if burst <= 1:
        return 0.0
    return min(burst * interactive_share, float(burst - 1))


@dataclass(frozen=True, slots=True)
class TakeResult:
    """One attempt to take a token: granted, or how long until one could be."""

    granted: bool
    wait_s: float
    #: True when the wait is a throttle block rather than an empty bucket.
    blocked: bool = False


def refill_and_take(
    *,
    tokens: float,
    elapsed_s: float,
    rate: float,
    burst: int,
    floor: float,
    blocked_for_s: float,
) -> tuple[float, TakeResult]:
    """The bucket arithmetic, shared by the in-memory and database buckets.

    Refills ``tokens`` for ``elapsed_s`` (capped at ``burst``), then takes one if
    that leaves at least ``floor`` (the share kept for the other lane). Returns
    the new token count and the outcome.
    """
    level = min(float(burst), tokens + rate * max(elapsed_s, 0.0))
    if blocked_for_s > 0:
        return level, TakeResult(granted=False, wait_s=blocked_for_s, blocked=True)
    if level - 1.0 >= floor - 1e-9:
        return level - 1.0, TakeResult(granted=True, wait_s=0.0)
    return level, TakeResult(granted=False, wait_s=(floor + 1.0 - level) / rate)


class RateBucket(Protocol):
    """A token bucket one source's requests draw from."""

    async def take(self, lane: FetchLane) -> TakeResult: ...

    async def block(self, seconds: float) -> None: ...


class MemoryRateBucket:
    """A process-local bucket (SQLite installs run one process)."""

    def __init__(
        self,
        *,
        rate: float,
        burst: int,
        interactive_share: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._rate = rate
        self._burst = burst
        self._reserve = background_reserve(burst, interactive_share)
        self._clock = clock
        self._tokens = float(burst)
        self._refilled = clock()
        self._blocked_until = 0.0

    async def take(self, lane: FetchLane) -> TakeResult:
        now = self._clock()
        floor = 0.0 if lane == "interactive" else self._reserve
        self._tokens, result = refill_and_take(
            tokens=self._tokens,
            elapsed_s=now - self._refilled,
            rate=self._rate,
            burst=self._burst,
            floor=floor,
            blocked_for_s=self._blocked_until - now,
        )
        self._refilled = now
        return result

    async def block(self, seconds: float) -> None:
        self._blocked_until = max(self._blocked_until, self._clock() + seconds)


class LaneSemaphore:
    """``concurrency`` slots, of which background requests may hold only some."""

    def __init__(self, concurrency: int, interactive_share: float) -> None:
        self._limit = concurrency
        self._background_limit = background_slots(concurrency, interactive_share)
        self._in_flight = 0
        self._background = 0
        self._changed = asyncio.Condition()

    @property
    def in_flight(self) -> int:
        return self._in_flight

    def _free(self, lane: FetchLane) -> bool:
        if self._in_flight >= self._limit:
            return False
        return lane == "interactive" or self._background < self._background_limit

    async def acquire(self, lane: FetchLane) -> None:
        """Wait for a free slot on ``lane`` and take it (cancellable)."""
        async with self._changed:
            await self._changed.wait_for(lambda: self._free(lane))
            self._in_flight += 1
            if lane == "background":
                self._background += 1

    async def release(self, lane: FetchLane) -> None:
        async with self._changed:
            self._in_flight -= 1
            if lane == "background":
                self._background -= 1
            self._changed.notify_all()

    @asynccontextmanager
    async def hold(self, lane: FetchLane) -> AsyncIterator[None]:
        await self.acquire(lane)
        try:
            yield
        finally:
            await self.release(lane)


class ProcessSourceLimits:
    """The ``SourceLimits`` of one source in this process."""

    def __init__(
        self,
        slug: str,
        *,
        concurrency: int,
        interactive_share: float,
        bucket: RateBucket | None = None,
        max_wait_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._slug = slug
        self._concurrency = concurrency
        self._interactive_share = interactive_share
        self._semaphore = LaneSemaphore(concurrency, interactive_share)
        self._semaphore_loop: asyncio.AbstractEventLoop | None = None
        self._bucket = bucket
        self._max_wait_s = max_wait_s
        self._clock = clock
        self._blocked_until = 0.0

    @property
    def in_flight(self) -> int:
        """Requests to this source in flight in this process."""
        return self._semaphore.in_flight

    def _gate(self) -> LaneSemaphore:
        """The concurrency gate for the running loop.

        Its ``asyncio.Condition`` binds to the first loop that waits on it, so a
        new loop (tests, a CLI command) gets a fresh gate, as it gets a fresh
        pooled client.
        """
        loop = asyncio.get_running_loop()
        if self._semaphore_loop is not loop:
            if self._semaphore_loop is not None:
                self._semaphore = LaneSemaphore(self._concurrency, self._interactive_share)
            self._semaphore_loop = loop
        return self._semaphore

    def _check_blocked(self) -> None:
        """Fail fast while this process knows the source is blocked by a ``429``."""
        remaining = self._blocked_until - self._clock()
        if remaining > 0:
            raise SourceUnavailableError(
                f"catalog source {self._slug!r} is throttled", retry_after_s=remaining
            )

    async def _token(self, lane: FetchLane, *, budget_s: float) -> None:
        if self._bucket is None:
            return
        waited = 0.0
        while True:
            result = await self._bucket.take(lane)
            if result.granted:
                return
            if result.blocked or waited + result.wait_s > budget_s:
                raise SourceUnavailableError(
                    f"catalog source {self._slug!r} is throttled",
                    retry_after_s=result.wait_s,
                )
            pause = max(result.wait_s, 0.01)
            await asyncio.sleep(pause)
            waited += pause

    @asynccontextmanager
    async def slot(self, lane: FetchLane) -> AsyncIterator[None]:
        """Hold one request's worth of capacity on ``lane``."""
        self._check_blocked()
        started = self._clock()
        gate = self._gate()
        try:
            async with asyncio.timeout(self._max_wait_s):
                await gate.acquire(lane)
        except TimeoutError:
            raise SourceUnavailableError(
                f"catalog source {self._slug!r} is at its concurrency limit",
                retry_after_s=self._max_wait_s,
            ) from None
        try:
            # A 429 may have landed while this request queued for its slot.
            self._check_blocked()
            budget = max(self._max_wait_s - (self._clock() - started), 0.0)
            await self._token(lane, budget_s=budget)
            metrics.record_bucket_wait(self._slug, lane, self._clock() - started)
            yield
        finally:
            await gate.release(lane)

    async def block(self, seconds: float | None) -> float:
        """Block both lanes for ``seconds`` (a default when ``None``); returns the block."""
        span = DEFAULT_THROTTLE_SECONDS if seconds is None else max(seconds, 0.0)
        # Always remembered in process (so queued requests fail fast without a
        # bucket round trip); shared with other processes through the bucket.
        self._blocked_until = max(self._blocked_until, self._clock() + span)
        if self._bucket is not None:
            await self._bucket.block(span)
        return span
