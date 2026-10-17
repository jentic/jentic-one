"""Unit tests for the broker route-discovery cache (#1713)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable

import pytest

from jentic_one.broker.core.discovery_cache import DiscoveryCache
from jentic_one.shared.broker.protocols import ResolveResult
from jentic_one.shared.config import BrokerConfig
from jentic_one.shared.schemas import APIReference, OperationInfo
from jentic_one.wiring import build_discovery_cache

URL = "https://api.acme.com/v1/pets/42"


def _result(op_id: str = "op_1") -> ResolveResult:
    return ResolveResult(
        operation=OperationInfo(id=op_id, path="/v1/pets/{id}", method="GET"),
        api=APIReference(vendor="acme.com", name="pets", version="v1"),
        path_params={"id": "42"},
        server_variables={"region": "eu"},
    )


class _Generation:
    """A controllable stand-in for the process routing generation."""

    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> int:
        return self.value


def _counting_loader(
    value: ResolveResult | None,
) -> tuple[Callable[[], Awaitable[ResolveResult | None]], list[int]]:
    calls: list[int] = []

    async def load() -> ResolveResult | None:
        calls.append(1)
        return value

    return load, calls


def _cache(
    *,
    ttl_s: float = 60.0,
    negative_ttl_s: float = 0.0,
    max_entries: int = 100,
    clock: Callable[[], float] = lambda: 1000.0,
) -> tuple[DiscoveryCache, _Generation]:
    generation = _Generation()
    cache = DiscoveryCache(
        ttl_s=ttl_s,
        negative_ttl_s=negative_ttl_s,
        max_entries=max_entries,
        generation=generation,
        clock=clock,
    )
    return cache, generation


async def test_hit_serves_repeat_lookup_without_loading() -> None:
    cache, _ = _cache()
    load, calls = _counting_loader(_result())

    first = await cache.resolve(method="GET", url=URL, revision_id=None, load=load)
    second = await cache.resolve(method="get", url=URL + "?page=2#x", revision_id=None, load=load)

    assert first == second == _result()
    # Method case, query string and fragment never change a resolution.
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("method", "url", "revision_id"),
    [
        ("POST", URL, None),
        ("GET", "https://other.acme.com/v1/pets/42", None),
        ("GET", "http://api.acme.com/v1/pets/42", None),
        ("GET", "https://api.acme.com:8443/v1/pets/42", None),
        ("GET", "https://api.acme.com/v1/pets/43", None),
        ("GET", URL, uuid.uuid4()),
    ],
)
async def test_miss_on_any_differing_lookup_input(
    method: str, url: str, revision_id: uuid.UUID | None
) -> None:
    cache, _ = _cache()
    load, calls = _counting_loader(_result())
    await cache.resolve(method="GET", url=URL, revision_id=None, load=load)

    await cache.resolve(method=method, url=url, revision_id=revision_id, load=load)

    assert len(calls) == 2


async def test_entry_expires_after_ttl() -> None:
    now = [1000.0]
    cache, _ = _cache(ttl_s=3.0, clock=lambda: now[0])
    load, calls = _counting_loader(_result())

    await cache.resolve(method="GET", url=URL, revision_id=None, load=load)
    now[0] += 2.9
    await cache.resolve(method="GET", url=URL, revision_id=None, load=load)
    assert len(calls) == 1

    now[0] += 0.2
    await cache.resolve(method="GET", url=URL, revision_id=None, load=load)
    assert len(calls) == 2


async def test_generation_bump_invalidates_every_entry() -> None:
    cache, generation = _cache()
    load, calls = _counting_loader(_result())
    other = "https://api.acme.com/v1/owners"
    await cache.resolve(method="GET", url=URL, revision_id=None, load=load)
    await cache.resolve(method="GET", url=other, revision_id=None, load=load)

    generation.value += 1
    await cache.resolve(method="GET", url=URL, revision_id=None, load=load)
    await cache.resolve(method="GET", url=other, revision_id=None, load=load)

    assert len(calls) == 4


async def test_lookup_racing_a_routing_commit_is_not_stored() -> None:
    """A read that began before a routing commit may carry pre-commit state."""
    cache, generation = _cache()
    calls: list[int] = []

    async def load_then_commit() -> ResolveResult | None:
        calls.append(1)
        generation.value += 1  # a promote commits while the read is in flight
        return _result("op_stale")

    stale = await cache.resolve(method="GET", url=URL, revision_id=None, load=load_then_commit)
    load, _ = _counting_loader(_result("op_fresh"))
    fresh = await cache.resolve(method="GET", url=URL, revision_id=None, load=load)

    assert stale is not None and stale.operation.id == "op_stale"
    assert fresh is not None and fresh.operation.id == "op_fresh"


async def test_miss_is_not_cached_by_default() -> None:
    cache, _ = _cache()
    load, calls = _counting_loader(None)

    assert await cache.resolve(method="GET", url=URL, revision_id=None, load=load) is None
    assert await cache.resolve(method="GET", url=URL, revision_id=None, load=load) is None

    assert len(calls) == 2
    assert len(cache) == 0


async def test_negative_ttl_bounds_a_cached_miss() -> None:
    now = [1000.0]
    cache, _ = _cache(ttl_s=30.0, negative_ttl_s=2.0, clock=lambda: now[0])
    load, calls = _counting_loader(None)

    await cache.resolve(method="GET", url=URL, revision_id=None, load=load)
    now[0] += 1.9
    await cache.resolve(method="GET", url=URL, revision_id=None, load=load)
    assert len(calls) == 1

    now[0] += 0.2
    await cache.resolve(method="GET", url=URL, revision_id=None, load=load)
    assert len(calls) == 2


async def test_lookup_error_is_not_cached() -> None:
    cache, _ = _cache()
    calls: list[int] = []

    async def failing() -> ResolveResult | None:
        calls.append(1)
        raise RuntimeError("registry unavailable")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cache.resolve(method="GET", url=URL, revision_id=None, load=failing)

    assert len(calls) == 2
    assert len(cache) == 0


async def test_concurrent_misses_share_one_lookup() -> None:
    cache, _ = _cache()
    release = asyncio.Event()
    calls: list[int] = []

    async def slow() -> ResolveResult | None:
        calls.append(1)
        await release.wait()
        return _result()

    tasks = [
        asyncio.create_task(cache.resolve(method="GET", url=URL, revision_id=None, load=slow))
        for _ in range(10)
    ]
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(*tasks)

    assert len(calls) == 1
    assert all(r == _result() for r in results)


async def test_lookup_after_a_generation_bump_never_joins_an_older_flight() -> None:
    """A lookup that starts after a routing commit reads post-commit state itself."""
    cache, generation = _cache()
    release = asyncio.Event()

    async def stale() -> ResolveResult | None:
        await release.wait()
        return _result("op_stale")

    async def fresh() -> ResolveResult | None:
        return _result("op_fresh")

    before = asyncio.create_task(cache.resolve(method="GET", url=URL, revision_id=None, load=stale))
    await asyncio.sleep(0)  # the pre-commit lookup is in flight
    generation.value += 1
    try:
        # Joining the pre-commit flight would wait on ``release``; the timeout
        # turns that into a failure instead of a hang.
        after = await asyncio.wait_for(
            cache.resolve(method="GET", url=URL, revision_id=None, load=fresh), timeout=1
        )
    finally:
        release.set()
    stale_result = await before

    assert after is not None and after.operation.id == "op_fresh"
    assert stale_result is not None and stale_result.operation.id == "op_stale"
    # Only the post-commit read is stored; the racing pre-commit one is dropped.
    load, calls = _counting_loader(_result("op_other"))
    cached = await cache.resolve(method="GET", url=URL, revision_id=None, load=load)
    assert cached is not None and cached.operation.id == "op_fresh"
    assert calls == []


async def test_cancelled_leader_stores_nothing_and_a_waiter_reruns_once() -> None:
    cache, _ = _cache()
    leader_started = asyncio.Event()
    calls: list[str] = []

    async def hangs() -> ResolveResult | None:
        calls.append("leader")
        leader_started.set()
        await asyncio.Event().wait()
        return _result("op_never")

    async def answers() -> ResolveResult | None:
        calls.append("waiter")
        return _result("op_waiter")

    leader = asyncio.create_task(cache.resolve(method="GET", url=URL, revision_id=None, load=hangs))
    await leader_started.wait()
    waiter = asyncio.create_task(
        cache.resolve(method="GET", url=URL, revision_id=None, load=answers)
    )
    await asyncio.sleep(0)  # the waiter joins the leader's flight
    assert len(cache) == 0
    leader.cancel()

    result = await waiter
    with pytest.raises(asyncio.CancelledError):
        await leader
    assert result is not None and result.operation.id == "op_waiter"
    assert calls == ["leader", "waiter"]
    assert len(cache) == 1


@pytest.mark.parametrize(
    "variant",
    [
        "https://API.Acme.COM/v1/pets/42",
        "HTTPS://api.acme.com/v1/pets/42",
        "https://api.acme.com:443/v1/pets/42",
    ],
)
async def test_scheme_host_case_and_default_port_share_an_entry(variant: str) -> None:
    """The key normalises exactly what the lookup normalises (case, default port)."""
    cache, _ = _cache()
    load, calls = _counting_loader(_result())

    await cache.resolve(method="GET", url=URL, revision_id=None, load=load)
    await cache.resolve(method="GET", url=variant, revision_id=None, load=load)

    assert len(calls) == 1


@pytest.mark.parametrize(
    "variant",
    [
        "http://api.acme.com:443/v1/pets/42",  # 443 is not http's default port
        "https://api.acme.com:4430/v1/pets/42",
        "https://user@api.acme.com/v1/pets/42",
        "https://api.acme.com/V1/pets/42",  # the path is case-sensitive
    ],
)
async def test_non_default_authority_or_path_case_keeps_its_own_entry(variant: str) -> None:
    cache, _ = _cache()
    load, calls = _counting_loader(_result())

    await cache.resolve(method="GET", url=URL, revision_id=None, load=load)
    await cache.resolve(method="GET", url=variant, revision_id=None, load=load)

    assert len(calls) == 2


async def test_lru_bound_evicts_least_recently_used() -> None:
    cache, _ = _cache(max_entries=2)
    load, calls = _counting_loader(_result())
    a, b, c = (f"https://api.acme.com/{p}" for p in "abc")

    await cache.resolve(method="GET", url=a, revision_id=None, load=load)
    await cache.resolve(method="GET", url=b, revision_id=None, load=load)
    await cache.resolve(method="GET", url=a, revision_id=None, load=load)  # a is now MRU
    await cache.resolve(method="GET", url=c, revision_id=None, load=load)  # evicts b
    assert len(cache) == 2
    assert len(calls) == 3

    await cache.resolve(method="GET", url=a, revision_id=None, load=load)
    assert len(calls) == 3
    await cache.resolve(method="GET", url=b, revision_id=None, load=load)
    assert len(calls) == 4


async def test_callers_get_independent_copies() -> None:
    cache, _ = _cache()
    load, _ = _counting_loader(_result())

    first = await cache.resolve(method="GET", url=URL, revision_id=None, load=load)
    assert first is not None
    first.path_params["id"] = "tampered"
    first.server_variables.clear()
    second = await cache.resolve(method="GET", url=URL, revision_id=None, load=load)

    assert second == _result()


def test_resolve_result_fields_are_pinned() -> None:
    """The key omits the query string because no ``ResolveResult`` field derives from it.

    A new field must be checked against that assumption (and ``_copy``) before
    it is added here.
    """
    assert set(ResolveResult.__dataclass_fields__) == {
        "operation",
        "api",
        "path_params",
        "server_variables",
        "server_variable_defaults",
        "server_variables_unresolved",
    }


def test_build_discovery_cache_ttl_zero_disables_it() -> None:
    assert build_discovery_cache(BrokerConfig(discovery_cache_ttl_s=0)) is None
    assert isinstance(build_discovery_cache(BrokerConfig()), DiscoveryCache)


@pytest.mark.parametrize(
    ("ttl_s", "negative_ttl_s", "max_entries"),
    [
        (0.0, 0.0, 1),
        (float("inf"), 0.0, 1),
        (float("nan"), 0.0, 1),
        (1.0, -1.0, 1),
        (1.0, 2.0, 1),  # a miss may not outlive a hit
        (1.0, float("nan"), 1),
        (1.0, 0.0, 0),
    ],
)
def test_invalid_parameters_rejected(ttl_s: float, negative_ttl_s: float, max_entries: int) -> None:
    with pytest.raises(ValueError):
        DiscoveryCache(ttl_s=ttl_s, negative_ttl_s=negative_ttl_s, max_entries=max_entries)
