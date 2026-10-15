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
