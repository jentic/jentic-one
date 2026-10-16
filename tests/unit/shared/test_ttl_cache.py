"""Tests for the bounded, single-flight ``AsyncTTLCache``."""

from __future__ import annotations

import asyncio

import pytest

from jentic_one.shared.ttl_cache import AsyncTTLCache


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Loader:
    def __init__(self, value: str = "v") -> None:
        self.value = value
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        return f"{self.value}{self.calls}"


@pytest.mark.asyncio
async def test_hit_within_ttl_runs_loader_once() -> None:
    clock = _Clock()
    cache: AsyncTTLCache[str, str] = AsyncTTLCache(max_entries=4, ttl_seconds=60, clock=clock)
    loader = _Loader()

    assert await cache.get_or_load("a", loader) == "v1"
    clock.now += 59
    assert await cache.get_or_load("a", loader) == "v1"
    assert loader.calls == 1


@pytest.mark.asyncio
async def test_expired_entry_reloads() -> None:
    clock = _Clock()
    cache: AsyncTTLCache[str, str] = AsyncTTLCache(max_entries=4, ttl_seconds=60, clock=clock)
    loader = _Loader()

    await cache.get_or_load("a", loader)
    clock.now += 60
    assert await cache.get_or_load("a", loader) == "v2"
    assert loader.calls == 2


@pytest.mark.asyncio
async def test_lru_eviction_bounds_entry_count() -> None:
    cache: AsyncTTLCache[str, str] = AsyncTTLCache(max_entries=2, ttl_seconds=60, clock=_Clock())
    loaders = {k: _Loader(k) for k in "abc"}

    await cache.get_or_load("a", loaders["a"])
    await cache.get_or_load("b", loaders["b"])
    await cache.get_or_load("a", loaders["a"])  # touch "a" so "b" is least recent
    await cache.get_or_load("c", loaders["c"])

    assert len(cache) == 2
    await cache.get_or_load("a", loaders["a"])
    await cache.get_or_load("b", loaders["b"])
    assert loaders["a"].calls == 1
    assert loaders["b"].calls == 2


@pytest.mark.asyncio
async def test_concurrent_misses_share_one_load() -> None:
    cache: AsyncTTLCache[str, str] = AsyncTTLCache(max_entries=4, ttl_seconds=60, clock=_Clock())
    release = asyncio.Event()
    calls = 0

    async def slow_loader() -> str:
        nonlocal calls
        calls += 1
        await release.wait()
        return "doc"

    async def _get(out: list[str]) -> None:
        out.append(await cache.get_or_load("a", slow_loader))

    results: list[str] = []
    async with asyncio.TaskGroup() as tg:
        for _ in range(5):
            tg.create_task(_get(results))
        await asyncio.sleep(0)
        release.set()

    assert results == ["doc"] * 5
    assert calls == 1


@pytest.mark.asyncio
async def test_loader_failure_is_not_cached() -> None:
    cache: AsyncTTLCache[str, str] = AsyncTTLCache(max_entries=4, ttl_seconds=60, clock=_Clock())
    attempts = 0

    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("upstream down")
        return "ok"

    with pytest.raises(RuntimeError):
        await cache.get_or_load("a", flaky)
    assert await cache.get_or_load("a", flaky) == "ok"
    assert len(cache) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("max_entries", "ttl"), [(0, 60), (4, 0)])
async def test_zero_bound_disables_caching(max_entries: int, ttl: float) -> None:
    cache: AsyncTTLCache[str, str] = AsyncTTLCache(
        max_entries=max_entries, ttl_seconds=ttl, clock=_Clock()
    )
    loader = _Loader()

    await cache.get_or_load("a", loader)
    await cache.get_or_load("a", loader)
    assert loader.calls == 2
    assert not cache.enabled
    assert len(cache) == 0


@pytest.mark.asyncio
async def test_clear_evicts_and_discards_in_flight_load() -> None:
    cache: AsyncTTLCache[str, str] = AsyncTTLCache(max_entries=4, ttl_seconds=60, clock=_Clock())
    await cache.get_or_load("a", _Loader())
    cache.clear()
    assert len(cache) == 0

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_loader() -> str:
        started.set()
        await release.wait()
        return "stale"

    async with asyncio.TaskGroup() as tg:
        task = tg.create_task(cache.get_or_load("b", slow_loader))
        await started.wait()
        cache.clear()
        release.set()

    # The caller still gets its value, but the pre-clear result is not stored.
    assert task.result() == "stale"
    assert len(cache) == 0


@pytest.mark.asyncio
async def test_concurrent_misses_share_one_failure() -> None:
    """Every waiter of a failing load gets its exception; the loader runs once."""
    cache: AsyncTTLCache[str, str] = AsyncTTLCache(max_entries=4, ttl_seconds=60, clock=_Clock())
    release = asyncio.Event()
    calls = 0

    async def failing_loader() -> str:
        nonlocal calls
        calls += 1
        await release.wait()
        raise RuntimeError("upstream down")

    tasks = [asyncio.create_task(cache.get_or_load("a", failing_loader)) for _ in range(5)]
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert calls == 1
    assert all(isinstance(r, RuntimeError) for r in results)
    # Failures are not remembered by default, so the next call reloads.
    with pytest.raises(RuntimeError):
        await cache.get_or_load("a", failing_loader)
    assert calls == 2


@pytest.mark.asyncio
async def test_cancelling_one_waiter_does_not_cancel_the_shared_load() -> None:
    cache: AsyncTTLCache[str, str] = AsyncTTLCache(max_entries=4, ttl_seconds=60, clock=_Clock())
    release = asyncio.Event()

    async def slow_loader() -> str:
        await release.wait()
        return "doc"

    first = asyncio.create_task(cache.get_or_load("a", slow_loader))
    second = asyncio.create_task(cache.get_or_load("a", slow_loader))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()

    assert await second == "doc"
    assert len(cache) == 1


@pytest.mark.asyncio
async def test_cancelling_the_last_waiter_cancels_the_load() -> None:
    cache: AsyncTTLCache[str, str] = AsyncTTLCache(max_entries=4, ttl_seconds=60, clock=_Clock())
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_loader() -> str:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return "never"

    waiter = asyncio.create_task(cache.get_or_load("a", slow_loader))
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    await asyncio.wait_for(cancelled.wait(), timeout=1)

    # A later caller starts a fresh load instead of joining the cancelled one.
    assert await cache.get_or_load("a", _Loader()) == "v1"


@pytest.mark.asyncio
async def test_failure_ttl_remembers_the_error_until_it_expires() -> None:
    clock = _Clock()
    cache: AsyncTTLCache[str, str] = AsyncTTLCache(
        max_entries=4, ttl_seconds=60, failure_ttl_seconds=30, clock=clock
    )
    calls = 0

    async def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("upstream down")
        return "ok"

    with pytest.raises(RuntimeError):
        await cache.get_or_load("a", flaky)
    clock.now += 29
    with pytest.raises(RuntimeError, match="upstream down"):
        await cache.get_or_load("a", flaky)
    assert calls == 1
    clock.now += 1
    assert await cache.get_or_load("a", flaky) == "ok"
    assert calls == 2


@pytest.mark.asyncio
async def test_clear_detaches_in_flight_load_from_later_callers() -> None:
    cache: AsyncTTLCache[str, str] = AsyncTTLCache(max_entries=4, ttl_seconds=60, clock=_Clock())
    started = asyncio.Event()
    release = asyncio.Event()

    async def stale_loader() -> str:
        started.set()
        await release.wait()
        return "stale"

    old = asyncio.create_task(cache.get_or_load("a", stale_loader))
    await started.wait()
    cache.clear()
    # A caller after clear() must not join the pre-clear load.
    assert await cache.get_or_load("a", _Loader("fresh")) == "fresh1"
    release.set()
    assert await old == "stale"
    assert await cache.get_or_load("a", _Loader("other")) == "fresh1"
