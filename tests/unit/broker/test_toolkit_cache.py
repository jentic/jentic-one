"""Unit tests for the TTL + single-flight toolkit-derivation cache (§05 R3)."""

from __future__ import annotations

import asyncio

import pytest

from jentic_one.broker.repos.caching_toolkit_deriver import CachingToolkitDeriver
from jentic_one.shared.broker.protocols import ToolkitDeriverProtocol


class _CountingDeriver:
    """A ToolkitDeriverProtocol stub that counts calls (no DB)."""

    def __init__(self, result: list[str] | None = None) -> None:
        self.result = result if result is not None else ["tk_1"]
        self.calls = 0
        self.gate: asyncio.Event | None = None

    async def derive_toolkits(
        self, *, agent_id: str, vendor: str, name: str, version: str
    ) -> list[str]:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        return list(self.result)

    async def any_toolkit_serves_api(self, *, vendor: str, name: str, version: str) -> bool:
        return bool(self.result)


def test_wrapper_satisfies_protocol() -> None:
    wrapper = CachingToolkitDeriver(_CountingDeriver())
    assert isinstance(wrapper, ToolkitDeriverProtocol)


@pytest.mark.asyncio
async def test_second_request_hits_cache() -> None:
    """A repeat (actor, vendor, name, version) is served from cache — no second lookup."""
    inner = _CountingDeriver(["tk_1", "tk_2"])
    cache = CachingToolkitDeriver(inner, cache_ttl_seconds=300.0)

    first = await cache.derive_toolkits(agent_id="a1", vendor="acme", name="api", version="1")
    second = await cache.derive_toolkits(agent_id="a1", vendor="acme", name="api", version="1")

    assert first == ["tk_1", "tk_2"]
    assert second == ["tk_1", "tk_2"]
    assert inner.calls == 1


@pytest.mark.asyncio
async def test_distinct_keys_each_lookup() -> None:
    """Different key tuples are cached independently."""
    inner = _CountingDeriver()
    cache = CachingToolkitDeriver(inner, cache_ttl_seconds=300.0)

    await cache.derive_toolkits(agent_id="a1", vendor="acme", name="api", version="1")
    await cache.derive_toolkits(agent_id="a2", vendor="acme", name="api", version="1")
    await cache.derive_toolkits(agent_id="a1", vendor="other", name="api", version="1")

    assert inner.calls == 3


@pytest.mark.asyncio
async def test_cache_expires_after_ttl() -> None:
    """After the TTL lapses the lookup runs again."""
    inner = _CountingDeriver()
    cache = CachingToolkitDeriver(inner, cache_ttl_seconds=0.01)

    await cache.derive_toolkits(agent_id="a1", vendor="acme", name="api", version="1")
    await asyncio.sleep(0.02)
    await cache.derive_toolkits(agent_id="a1", vendor="acme", name="api", version="1")

    assert inner.calls == 2


@pytest.mark.asyncio
async def test_concurrent_misses_single_flight_to_one_lookup() -> None:
    """Concurrent misses for one key collapse to a single Admin+Control lookup."""
    inner = _CountingDeriver(["tk_1"])
    inner.gate = asyncio.Event()
    cache = CachingToolkitDeriver(inner, cache_ttl_seconds=300.0)

    async def call() -> list[str]:
        return await cache.derive_toolkits(agent_id="a1", vendor="acme", name="api", version="1")

    tasks = [asyncio.create_task(call()) for _ in range(15)]
    await asyncio.sleep(0)
    inner.gate.set()
    results = await asyncio.gather(*tasks)

    assert inner.calls == 1
    assert all(r == ["tk_1"] for r in results)
    # And a subsequent call is now a pure cache hit.
    await cache.derive_toolkits(agent_id="a1", vendor="acme", name="api", version="1")
    assert inner.calls == 1


@pytest.mark.asyncio
async def test_returned_list_is_isolated_from_cache() -> None:
    """Mutating a returned list does not corrupt the cached entry."""
    inner = _CountingDeriver(["tk_1"])
    cache = CachingToolkitDeriver(inner, cache_ttl_seconds=300.0)

    first = await cache.derive_toolkits(agent_id="a1", vendor="acme", name="api", version="1")
    first.append("tk_injected")
    second = await cache.derive_toolkits(agent_id="a1", vendor="acme", name="api", version="1")

    assert second == ["tk_1"]


@pytest.mark.asyncio
async def test_clear_drops_entries() -> None:
    inner = _CountingDeriver()
    cache = CachingToolkitDeriver(inner, cache_ttl_seconds=300.0)

    await cache.derive_toolkits(agent_id="a1", vendor="acme", name="api", version="1")
    cache.clear()
    await cache.derive_toolkits(agent_id="a1", vendor="acme", name="api", version="1")

    assert inner.calls == 2
