"""Unit tests for the shared TTL + LRU caches (#1960)."""

from __future__ import annotations

import asyncio
import math

import pytest

from jentic_one.shared.cache import TTLLRU, AsyncTTLCache, CacheStats, ttl_lru


class _Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class _GatedLoader:
    """A loader that returns a scripted value and can block until released."""

    def __init__(self, *values: str) -> None:
        self._values = list(values)
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    def hold(self) -> None:
        self.release.clear()

    async def __call__(self) -> str:
        self.calls += 1
        value = self._values[min(self.calls, len(self._values)) - 1]
        self.started.set()
        await self.release.wait()
        return value


# ── TTLLRU ──────────────────────────────────────────────────────────────────


def test_ttllru_expires_at_deadline() -> None:
    clock = _Clock()
    store: TTLLRU[str, int] = TTLLRU(max_entries=4, clock=clock)
    store.put("a", 1, expires_at=clock.now + 5)

    entry = store.get("a")
    assert entry is not None and entry.value == 1
    clock.now += 5
    assert store.get("a") is None
    assert "a" not in store


def test_ttllru_evicts_least_recently_used() -> None:
    store: TTLLRU[str, int] = TTLLRU(max_entries=2)
    store.put("a", 1, expires_at=math.inf)
    store.put("b", 2, expires_at=math.inf)
    assert store.get("a") is not None  # "b" is now the LRU entry
    assert store.put("c", 3, expires_at=math.inf) == 1

    assert list(store) == ["a", "c"]
    assert store.evictions == 1


def test_ttllru_rejects_non_positive_bound() -> None:
    with pytest.raises(ValueError, match="max_entries"):
        TTLLRU[str, int](max_entries=0)


# ── AsyncTTLCache: hits, TTL, LRU ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_hit_within_ttl_skips_loader() -> None:
    clock = _Clock()
    cache: AsyncTTLCache[str, str] = AsyncTTLCache(ttl_seconds=10, max_entries=8, clock=clock)
    loader = _GatedLoader("v1", "v2")

    assert await cache.get_or_load("k", loader) == "v1"
    clock.now += 9.9
    assert await cache.get_or_load("k", loader) == "v1"
    clock.now += 0.1
    assert await cache.get_or_load("k", loader) == "v2"

    assert loader.calls == 2
    assert cache.stats == CacheStats(hits=1, misses=2, evictions=0)


@pytest.mark.asyncio
async def test_entry_is_stamped_before_the_load() -> None:
    """A slow load does not extend the entry's life: age counts from when it began."""
    clock = _Clock()
    cache: AsyncTTLCache[str, str] = AsyncTTLCache(ttl_seconds=10, max_entries=8, clock=clock)
    loader = _GatedLoader("v1", "v2")
    loader.hold()

    task = asyncio.create_task(cache.get_or_load("k", loader))
    await loader.started.wait()
    clock.now += 8  # the source read takes 8 s
    loader.release.set()
    assert await task == "v1"

    clock.now += 2  # 10 s after the load began, 2 s after it stored
    assert await cache.get_or_load("k", loader) == "v2"
    assert loader.calls == 2


@pytest.mark.asyncio
async def test_lru_bound_evicts_and_counts() -> None:
    cache: AsyncTTLCache[int, int] = AsyncTTLCache(ttl_seconds=60, max_entries=3)

    async def load_value(n: int) -> int:
        return n

    for n in range(5):
        await cache.get_or_load(n, lambda n=n: load_value(n))  # type: ignore[misc]

    assert len(cache) == 3
    assert cache.keys() == [2, 3, 4]
    assert cache.stats.evictions == 2


@pytest.mark.asyncio
async def test_a_hit_promotes_an_entry_out_of_the_eviction_seat() -> None:
    """Reading a key through ``get_or_load`` makes it most recently used, so the
    key not re-read is the one evicted and reloaded."""
    cache: AsyncTTLCache[str, str] = AsyncTTLCache(ttl_seconds=60, max_entries=2)
    calls: list[str] = []

    async def load(key: str) -> str:
        calls.append(key)
        return key.upper()

    assert await cache.get_or_load("a", lambda: load("a")) == "A"
    assert await cache.get_or_load("b", lambda: load("b")) == "B"
    assert await cache.get_or_load("a", lambda: load("a")) == "A"  # "b" is now LRU
    assert await cache.get_or_load("c", lambda: load("c")) == "C"  # evicts "b"

    assert cache.keys() == ["a", "c"]
    assert await cache.get_or_load("b", lambda: load("b")) == "B"
    assert calls == ["a", "b", "c", "b"]


@pytest.mark.asyncio
async def test_zero_ttl_never_stores_but_still_coalesces() -> None:
    cache: AsyncTTLCache[str, str] = AsyncTTLCache(ttl_seconds=0, max_entries=8)
    loader = _GatedLoader("v")
    loader.hold()

    tasks = [asyncio.create_task(cache.get_or_load("k", loader)) for _ in range(5)]
    await loader.started.wait()
    loader.release.set()
    assert await asyncio.gather(*tasks) == ["v"] * 5

    assert loader.calls == 1
    assert len(cache) == 0


@pytest.mark.asyncio
async def test_ttl_for_controls_per_value_lifetime() -> None:
    clock = _Clock()
    lifetimes = {"forever": math.inf, "skip": 0.0, "short": 1.0}
    cache: AsyncTTLCache[str, str] = AsyncTTLCache(
        max_entries=8, clock=clock, ttl_for=lifetimes.__getitem__
    )

    for value in lifetimes:

        async def load(value: str = value) -> str:
            return value

        await cache.get_or_load(value, load)

    assert "skip" not in cache
    clock.now += 1
    assert cache.get("short") is None
    clock.now += 10**9
    assert cache.get("forever") == "forever"


@pytest.mark.asyncio
async def test_loader_failure_is_not_cached() -> None:
    cache: AsyncTTLCache[str, str] = AsyncTTLCache(ttl_seconds=60, max_entries=8)
    calls = 0

    async def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        return "ok"

    with pytest.raises(RuntimeError):
        await cache.get_or_load("k", flaky)
    assert "k" not in cache
    assert await cache.get_or_load("k", flaky) == "ok"


@pytest.mark.asyncio
async def test_concurrent_misses_share_one_load() -> None:
    cache: AsyncTTLCache[str, str] = AsyncTTLCache(ttl_seconds=60, max_entries=8)
    loader = _GatedLoader("v")
    loader.hold()

    tasks = [asyncio.create_task(cache.get_or_load("k", loader)) for _ in range(20)]
    await loader.started.wait()
    loader.release.set()
    assert set(await asyncio.gather(*tasks)) == {"v"}
    assert loader.calls == 1


# ── AsyncTTLCache: invalidation ─────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["key", "prefix", "where", "clear"])
async def test_invalidation_during_in_flight_load_is_not_undone(how: str) -> None:
    """Pins #1960: an invalidation that lands while a load is in flight sticks.

    The load began before the invalidation, so its result is pre-invalidation
    state. It still answers the callers already waiting on it, but it must not
    be stored, and a caller arriving after the invalidation must run a fresh
    load rather than join the stale one.
    """
    cache: AsyncTTLCache[str, str] = AsyncTTLCache(ttl_seconds=60, max_entries=8)
    loader = _GatedLoader("stale", "fresh")
    loader.hold()

    stale_waiter = asyncio.create_task(cache.get_or_load("agent:1", loader))
    await loader.started.wait()

    if how == "key":
        cache.invalidate("agent:1")
    elif how == "prefix":
        cache.invalidate_prefix("agent:")
    elif how == "where":
        cache.invalidate_where(lambda k, _v: k == "agent:1")
    else:
        cache.clear()

    loader.release.set()
    assert await stale_waiter == "stale"
    assert "agent:1" not in cache

    assert await cache.get_or_load("agent:1", loader) == "fresh"
    assert loader.calls == 2
    assert cache.get("agent:1") == "fresh"


@pytest.mark.asyncio
async def test_caller_after_invalidation_does_not_join_the_stale_load() -> None:
    cache: AsyncTTLCache[str, str] = AsyncTTLCache(ttl_seconds=60, max_entries=8)
    stale = _GatedLoader("stale")
    stale.hold()
    fresh = _GatedLoader("fresh")

    first = asyncio.create_task(cache.get_or_load("k", stale))
    await stale.started.wait()
    cache.invalidate("k")

    # The stale load is still running; this caller must not be handed its result.
    assert await cache.get_or_load("k", fresh) == "fresh"
    stale.release.set()
    assert await first == "stale"
    assert cache.get("k") == "fresh"


@pytest.mark.asyncio
async def test_invalidate_prefix_matches_str_keys() -> None:
    cache: AsyncTTLCache[str, int] = AsyncTTLCache(ttl_seconds=60, max_entries=8)
    for key in ("a\x001", "a\x002", "b\x001"):
        cache.put(key, 1)
    assert cache.invalidate_prefix("a\x00") == 2
    assert cache.keys() == ["b\x001"]


@pytest.mark.asyncio
async def test_invalidate_where_sees_values() -> None:
    cache: AsyncTTLCache[str, str | None] = AsyncTTLCache(ttl_seconds=60, max_entries=8)
    cache.put("t1", "usr_a")
    cache.put("t2", None)
    cache.put("t3", "usr_b")

    removed = cache.invalidate_where(lambda _k, v: v is None or v == "usr_a")

    assert removed == 2
    assert cache.keys() == ["t3"]


@pytest.mark.asyncio
async def test_invalidation_bumps_generation_even_when_nothing_is_cached() -> None:
    cache: AsyncTTLCache[str, str] = AsyncTTLCache(ttl_seconds=60, max_entries=8)
    before = cache.generation
    assert cache.invalidate("missing") is False
    assert cache.generation == before + 1


# ── AsyncTTLCache: metrics ──────────────────────────────────────────────────


class _FakeCounter:
    def __init__(self, name: str) -> None:
        self.name = name
        self.total = 0

    def add(self, amount: int, *args: object, **kwargs: object) -> None:
        self.total += amount


class _FakeMeter:
    def __init__(self, name: str) -> None:
        self.name = name
        self.counters: dict[str, _FakeCounter] = {}

    def create_counter(self, name: str, **kwargs: object) -> _FakeCounter:
        counter = _FakeCounter(name)
        self.counters[name] = counter
        return counter


@pytest.fixture()
def fake_meters(monkeypatch: pytest.MonkeyPatch) -> dict[str, _FakeMeter]:
    """Capture the meters and counters the cache creates, isolated per test."""
    meters: dict[str, _FakeMeter] = {}

    def fake_get_meter(name: str) -> _FakeMeter:
        return meters.setdefault(name, _FakeMeter(name))

    monkeypatch.setattr(ttl_lru, "get_meter", fake_get_meter)
    monkeypatch.setattr(ttl_lru, "_counters", {})
    return meters


@pytest.mark.asyncio
async def test_counters_are_created_on_the_owners_meter(
    fake_meters: dict[str, _FakeMeter],
) -> None:
    """Pins #1960: a migrated cache keeps its counter names *and* their meter scope."""
    cache: AsyncTTLCache[str, str] = AsyncTTLCache(
        ttl_seconds=60,
        max_entries=1,
        metrics_scope="admin",
        metrics_prefix="monitoring.cache",
    )

    async def load() -> str:
        return "v"

    await cache.get_or_load("a", load)  # miss
    await cache.get_or_load("a", load)  # hit
    await cache.get_or_load("b", load)  # miss, evicts "a" from the size-1 store

    assert list(fake_meters) == ["admin"]
    counters = fake_meters["admin"].counters
    assert counters["monitoring.cache.hits"].total == 1
    assert counters["monitoring.cache.misses"].total == 2
    assert counters["monitoring.cache.evictions"].total == 1


@pytest.mark.asyncio
async def test_instances_sharing_scope_and_prefix_share_counters(
    fake_meters: dict[str, _FakeMeter],
) -> None:
    def make() -> AsyncTTLCache[str, str]:
        return AsyncTTLCache(
            ttl_seconds=60, max_entries=4, metrics_scope="admin", metrics_prefix="monitoring.cache"
        )

    async def load() -> str:
        return "v"

    first, second = make(), make()
    await first.get_or_load("k", load)
    await second.get_or_load("k", load)

    counters = fake_meters["admin"].counters
    assert counters["monitoring.cache.misses"].total == 2
    assert first.stats.misses == 1 and second.stats.misses == 1


def test_metrics_scope_and_prefix_go_together() -> None:
    with pytest.raises(ValueError, match="metrics_scope"):
        AsyncTTLCache[str, str](ttl_seconds=60, max_entries=1, metrics_prefix="x")


def test_exactly_one_ttl_source_is_required() -> None:
    with pytest.raises(ValueError, match="ttl_seconds or ttl_for"):
        AsyncTTLCache[str, str](max_entries=1)
    with pytest.raises(ValueError, match="ttl_seconds or ttl_for"):
        AsyncTTLCache[str, str](max_entries=1, ttl_seconds=1, ttl_for=lambda _v: 1.0)
