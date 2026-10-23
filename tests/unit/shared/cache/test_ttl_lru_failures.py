"""Negative caching and per-caller cancellation in ``AsyncTTLCache`` (#1960).

The failure-caching behaviour here backs the registry's catalog preview cache
(``Context.catalog_preview_cache``): a spec that cannot be fetched or parsed is
remembered for ``catalog.preview_cache_failure_ttl_seconds`` so repeated
previews of a broken spec do not refetch it, and the remembered failure must not
pin the loader's locals (a capped response body) for that whole window.
"""

from __future__ import annotations

import asyncio
import gc
import weakref

import pytest

from jentic_one.shared.cache import AsyncTTLCache, CacheStats, ttl_lru


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


def _cache(**kwargs: object) -> AsyncTTLCache[str, str]:
    return AsyncTTLCache(max_entries=4, ttl_seconds=60, clock=_Clock(), **kwargs)  # type: ignore[arg-type]


# ── failures are shared, and not remembered by default ───────────────────────


@pytest.mark.asyncio
async def test_concurrent_misses_share_one_failure() -> None:
    """Every waiter of a failing load gets its exception; the loader runs once."""
    cache = _cache()
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


# ── failure_ttl_seconds: negative caching ────────────────────────────────────


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
async def test_remembered_failure_counts_as_a_hit() -> None:
    """A call answered from the negative cache ran no loader, so it is a hit."""
    cache = _cache(failure_ttl_seconds=30)

    async def failing() -> str:
        raise RuntimeError("upstream down")

    for _ in range(3):
        with pytest.raises(RuntimeError):
            await cache.get_or_load("a", failing)

    assert cache.stats == CacheStats(hits=2, misses=1, evictions=0)


@pytest.mark.asyncio
async def test_failure_from_a_fenced_load_is_not_remembered() -> None:
    """A failure derived before an invalidation must not outlive it (#1960).

    The load began under the previous generation, so its verdict — error or
    value — is pre-invalidation state: the waiter still sees the exception, but
    the next caller retries instead of being served the stale failure.
    """
    cache = _cache(failure_ttl_seconds=30)
    started = asyncio.Event()
    release = asyncio.Event()

    async def failing() -> str:
        started.set()
        await release.wait()
        raise RuntimeError("upstream down")

    waiter = asyncio.create_task(cache.get_or_load("a", failing))
    await started.wait()
    cache.invalidate("a")
    release.set()
    with pytest.raises(RuntimeError):
        await waiter

    loader = _Loader()
    assert await cache.get_or_load("a", loader) == "v1"
    assert loader.calls == 1


@pytest.mark.asyncio
async def test_invalidation_forgets_remembered_failures() -> None:
    cache = _cache(failure_ttl_seconds=30)

    async def failing() -> str:
        raise RuntimeError("upstream down")

    with pytest.raises(RuntimeError):
        await cache.get_or_load("a", failing)
    cache.clear()

    loader = _Loader()
    assert await cache.get_or_load("a", loader) == "v1"


class _Body:
    """A weak-referenceable stand-in for a large buffered upstream body."""

    def __init__(self) -> None:
        self.data = b"x" * (1024 * 1024)


@pytest.mark.asyncio
async def test_remembered_failure_does_not_retain_loader_locals() -> None:
    """A remembered failure must not pin the loader's frames: its body buffer is
    collected while the failure is still cached, and a hit raises a fresh,
    unchained instance of the same type and message."""
    cache = _cache(failure_ttl_seconds=30)
    body_ref: weakref.ref[_Body] | None = None

    async def failing() -> str:
        nonlocal body_ref
        body = _Body()
        body_ref = weakref.ref(body)
        try:
            raise ValueError("bad json")
        except ValueError as exc:
            raise RuntimeError("upstream unusable") from exc

    try:
        await cache.get_or_load("a", failing)
    except RuntimeError as first:
        assert isinstance(first.__cause__, ValueError)
    # Let the loop run the load task's done-callbacks, which still hold it.
    await asyncio.sleep(0)
    gc.collect()
    assert body_ref is not None
    assert body_ref() is None

    with pytest.raises(RuntimeError, match="upstream unusable") as hit:
        await cache.get_or_load("a", failing)
    assert hit.value.__cause__ is None
    assert hit.value.__context__ is None


@pytest.mark.asyncio
async def test_remembered_failure_drops_non_scalar_args() -> None:
    """Non-scalar exception args (e.g. a body) are reduced to the message; a type
    that cannot be rebuilt from its args still fails, as ``RuntimeError``."""

    class _RichError(Exception):
        def __init__(self, payload: bytes, status: int) -> None:
            super().__init__(payload, status)

    cache = _cache(failure_ttl_seconds=30)

    async def failing() -> str:
        raise _RichError(b"body", 502)

    with pytest.raises(_RichError):
        await cache.get_or_load("a", failing)
    with pytest.raises(RuntimeError) as hit:
        await cache.get_or_load("a", failing)
    assert hit.value.args == (str(_RichError(b"body", 502)),)


@pytest.mark.asyncio
async def test_remembered_failure_caps_long_string_args() -> None:
    """A long ``str`` arg is truncated in the remembered failure; other scalars
    are kept as they are."""
    cache = _cache(failure_ttl_seconds=30)

    async def failing() -> str:
        raise ValueError("x" * 10_000, 502)

    with pytest.raises(ValueError):
        await cache.get_or_load("a", failing)
    with pytest.raises(ValueError) as hit:
        await cache.get_or_load("a", failing)
    assert hit.value.args == ("x" * ttl_lru._MAX_FALLBACK_MESSAGE, 502)


# ── cancellation is per caller ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancelling_one_waiter_does_not_cancel_the_shared_load() -> None:
    """A request that goes away must not make the others refetch.

    The preview cache sits on a request path, where a client disconnect or a
    request timeout cancels one caller while others are still awaiting the same
    upstream fetch.
    """
    cache = _cache()
    release = asyncio.Event()
    calls = 0

    async def slow_loader() -> str:
        nonlocal calls
        calls += 1
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
    assert calls == 1
    assert len(cache) == 1


@pytest.mark.asyncio
async def test_cancelling_the_first_caller_does_not_cancel_the_load() -> None:
    """The caller that started the load is not privileged: its cancellation
    leaves the load running for everyone else rather than restarting it."""
    cache = _cache()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def slow_loader() -> str:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return "doc"

    first = asyncio.create_task(cache.get_or_load("a", slow_loader))
    await started.wait()
    others = [asyncio.create_task(cache.get_or_load("a", slow_loader)) for _ in range(3)]
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()

    assert await asyncio.gather(*others) == ["doc"] * 3
    assert calls == 1


@pytest.mark.asyncio
async def test_cancelling_the_last_waiter_cancels_the_load() -> None:
    cache = _cache()
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
