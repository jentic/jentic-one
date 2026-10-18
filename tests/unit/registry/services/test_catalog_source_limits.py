"""Unit tests for the per-source fetch limits (``catalog/sources/limits.py``).

Covers the token-bucket arithmetic shared by the in-memory and database buckets,
the lane reservation of the concurrency gate, and how ``ProcessSourceLimits``
fails fast (``SourceUnavailableError`` with the remaining wait) instead of
holding a caller while the source is throttled.
"""

from __future__ import annotations

import asyncio

import pytest

from jentic_one.registry.services.catalog.sources.limits import (
    DEFAULT_THROTTLE_SECONDS,
    LaneSemaphore,
    MemoryRateBucket,
    ProcessSourceLimits,
    background_reserve,
    background_slots,
    refill_and_take,
)
from jentic_one.registry.services.catalog.sources.protocol import SourceUnavailableError


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# ── arithmetic ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("concurrency", "share", "expected"),
    [(1, 0.5, 1), (2, 0.5, 1), (4, 0.5, 2), (4, 0.0, 4), (5, 0.5, 2), (8, 0.9, 1)],
)
def test_background_slots(concurrency: int, share: float, expected: int) -> None:
    """The interactive share is reserved (rounded up), but background always keeps one."""
    assert background_slots(concurrency, share) == expected


def test_refill_caps_at_burst_and_takes_one() -> None:
    tokens, result = refill_and_take(
        tokens=1.0, elapsed_s=100.0, rate=2.0, burst=4, floor=0.0, blocked_for_s=0.0
    )
    assert result.granted
    assert tokens == pytest.approx(3.0)


def test_take_refused_below_floor_reports_wait() -> None:
    """A background take may not dip into the interactive reserve; the wait is to the floor."""
    tokens, result = refill_and_take(
        tokens=2.0, elapsed_s=0.0, rate=1.0, burst=4, floor=2.0, blocked_for_s=0.0
    )
    assert not result.granted
    assert not result.blocked
    assert tokens == pytest.approx(2.0)
    assert result.wait_s == pytest.approx(1.0)


def test_blocked_bucket_refuses_with_block_remaining() -> None:
    _tokens, result = refill_and_take(
        tokens=4.0, elapsed_s=0.0, rate=1.0, burst=4, floor=0.0, blocked_for_s=7.5
    )
    assert not result.granted
    assert result.blocked
    assert result.wait_s == pytest.approx(7.5)


# ── in-memory bucket ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_memory_bucket_keeps_the_interactive_reserve() -> None:
    clock = _Clock()
    bucket = MemoryRateBucket(rate=1.0, burst=4, interactive_share=0.5, clock=clock)
    assert (await bucket.take("background")).granted
    assert (await bucket.take("background")).granted
    # Two left, both reserved for interactive requests.
    assert not (await bucket.take("background")).granted
    assert (await bucket.take("interactive")).granted
    assert (await bucket.take("interactive")).granted
    assert not (await bucket.take("interactive")).granted
    clock.now += 1.0
    assert (await bucket.take("interactive")).granted


@pytest.mark.asyncio
async def test_memory_bucket_block_refuses_until_it_lapses() -> None:
    clock = _Clock()
    bucket = MemoryRateBucket(rate=10.0, burst=4, interactive_share=0.0, clock=clock)
    await bucket.block(3.0)
    result = await bucket.take("interactive")
    assert result.blocked
    assert result.wait_s == pytest.approx(3.0)
    clock.now += 3.5
    assert (await bucket.take("interactive")).granted


# ── concurrency gate ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_background_cannot_take_reserved_slots_but_interactive_can() -> None:
    sem = LaneSemaphore(4, 0.5)
    release = asyncio.Event()
    started: list[str] = []

    async def _hold(lane: str) -> None:
        async with sem.hold(lane):  # type: ignore[arg-type]
            started.append(lane)
            await release.wait()

    tasks = [asyncio.create_task(_hold("background")) for _ in range(3)]
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert started.count("background") == 2  # the third waits: 2 slots are reserved
    interactive = [asyncio.create_task(_hold("interactive")) for _ in range(2)]
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert started.count("interactive") == 2
    assert sem.in_flight == 4
    release.set()
    await asyncio.gather(*tasks, *interactive)
    assert sem.in_flight == 0
    assert started.count("background") == 3


# ── ProcessSourceLimits ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_block_without_bucket_fails_fast_with_retry_after() -> None:
    clock = _Clock()
    limits = ProcessSourceLimits("corp", concurrency=2, interactive_share=0.5, clock=clock)
    assert await limits.block(None) == DEFAULT_THROTTLE_SECONDS
    with pytest.raises(SourceUnavailableError) as raised:
        async with limits.slot("interactive"):
            pass
    assert raised.value.retry_after_s == pytest.approx(DEFAULT_THROTTLE_SECONDS)
    assert limits.in_flight == 0  # the failed request released its slot
    clock.now += DEFAULT_THROTTLE_SECONDS + 0.1
    async with limits.slot("interactive"):
        assert limits.in_flight == 1


@pytest.mark.asyncio
async def test_bucket_wait_beyond_max_fails_fast() -> None:
    clock = _Clock()
    bucket = MemoryRateBucket(rate=0.01, burst=1, interactive_share=0.0, clock=clock)
    limits = ProcessSourceLimits(
        "corp", concurrency=2, interactive_share=0.0, bucket=bucket, max_wait_s=1.0, clock=clock
    )
    async with limits.slot("interactive"):
        pass
    with pytest.raises(SourceUnavailableError) as raised:
        async with limits.slot("interactive"):
            pass
    assert raised.value.retry_after_s == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_short_bucket_wait_is_waited_out() -> None:
    bucket = MemoryRateBucket(rate=50.0, burst=1, interactive_share=0.0)
    limits = ProcessSourceLimits(
        "corp", concurrency=2, interactive_share=0.0, bucket=bucket, max_wait_s=5.0
    )
    async with limits.slot("interactive"):
        pass
    async with limits.slot("interactive"):  # ~20 ms for the next token
        pass


@pytest.mark.asyncio
async def test_block_with_bucket_blocks_the_bucket() -> None:
    clock = _Clock()
    bucket = MemoryRateBucket(rate=10.0, burst=4, interactive_share=0.0, clock=clock)
    limits = ProcessSourceLimits(
        "corp", concurrency=2, interactive_share=0.0, bucket=bucket, clock=clock
    )
    assert await limits.block(12.0) == 12.0
    with pytest.raises(SourceUnavailableError) as raised:
        async with limits.slot("background"):
            pass
    assert raised.value.retry_after_s == pytest.approx(12.0)
    # The block reached the bucket too, so other processes sharing it see it.
    taken = await bucket.take("interactive")
    assert taken.blocked
    assert taken.wait_s == pytest.approx(12.0)


# ── small bursts (the reserve never starves the background lane) ─────────────


@pytest.mark.parametrize(
    ("burst", "share", "expected"),
    [(1, 0.5, 0.0), (1, 0.99, 0.0), (2, 0.5, 1.0), (2, 0.9, 1.0), (4, 0.5, 2.0), (4, 0.9, 3.0)],
)
def test_background_reserve_leaves_one_token(burst: int, share: float, expected: float) -> None:
    assert background_reserve(burst, share) == pytest.approx(expected)


@pytest.mark.asyncio
@pytest.mark.parametrize(("burst", "share"), [(1, 0.5), (2, 0.9), (4, 0.9)])
async def test_full_small_bucket_grants_background(burst: int, share: float) -> None:
    """With any burst and share, a full bucket still grants a background token."""
    bucket = MemoryRateBucket(rate=1.0, burst=burst, interactive_share=share, clock=_Clock())
    assert (await bucket.take("background")).granted


# ── slot waits are bounded too ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_slot_wait_beyond_max_fails_fast() -> None:
    """A request queued behind a full concurrency gate fails after ``max_wait_s``."""
    limits = ProcessSourceLimits("corp", concurrency=1, interactive_share=0.0, max_wait_s=0.2)
    held = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        async with limits.slot("interactive"):
            held.set()
            await release.wait()

    task = asyncio.create_task(holder())
    await held.wait()
    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(SourceUnavailableError) as raised:
        async with limits.slot("interactive"):
            pass
    assert loop.time() - started < 1.0
    assert raised.value.retry_after_s == pytest.approx(0.2)
    assert limits.in_flight == 1  # the timed-out waiter took nothing
    release.set()
    await task
    assert limits.in_flight == 0


@pytest.mark.asyncio
async def test_block_while_queued_fails_the_waiter_fast() -> None:
    """A 429 that lands while a request waits for its slot fails that request."""
    limits = ProcessSourceLimits("corp", concurrency=1, interactive_share=0.0, max_wait_s=5.0)
    held = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        async with limits.slot("interactive"):
            held.set()
            await release.wait()

    task = asyncio.create_task(holder())
    await held.wait()

    async def waiter() -> None:
        async with limits.slot("interactive"):
            pass

    queued = asyncio.create_task(waiter())
    await asyncio.sleep(0)
    await limits.block(3.0)
    release.set()
    await task
    with pytest.raises(SourceUnavailableError):
        await queued
    assert limits.in_flight == 0
