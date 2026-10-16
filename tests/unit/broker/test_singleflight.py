"""Unit tests for the async single-flight request-coalescing helper (§05 R3.1)."""

from __future__ import annotations

import asyncio

import pytest

from jentic_one.broker.core.singleflight import SingleFlight


@pytest.mark.asyncio
async def test_concurrent_misses_collapse_to_one_call() -> None:
    """N concurrent callers for one key trigger exactly one underlying lookup."""
    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def lookup() -> str:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return "value"

    sf: SingleFlight[str] = SingleFlight()

    async def call() -> str:
        return await sf.do("k", lookup)

    tasks = [asyncio.create_task(call()) for _ in range(20)]
    await started.wait()
    # All callers are now in-flight on the single shared future.
    assert sf.in_flight_count == 1
    release.set()
    results = await asyncio.gather(*tasks)

    assert calls == 1
    assert results == ["value"] * 20
    assert sf.in_flight_count == 0


@pytest.mark.asyncio
async def test_failure_propagates_to_all_waiters_and_pins_nothing() -> None:
    """A failing lookup raises in every waiter and leaves no pinned future."""
    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def failing() -> str:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        raise RuntimeError("boom")

    sf: SingleFlight[str] = SingleFlight()

    async def call() -> str:
        return await sf.do("k", failing)

    tasks = [asyncio.create_task(call()) for _ in range(5)]
    await started.wait()
    release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert calls == 1
    assert all(isinstance(r, RuntimeError) for r in results)
    # The poisoned future is gone — nothing pinned.
    assert sf.in_flight_count == 0

    # A subsequent call re-runs the lookup (no poisoned future served).
    async def ok() -> str:
        nonlocal calls
        calls += 1
        return "recovered"

    assert await sf.do("k", ok) == "recovered"
    assert calls == 2


@pytest.mark.asyncio
async def test_distinct_keys_do_not_collapse() -> None:
    """Different keys run independent lookups."""
    calls: dict[str, int] = {}
    release = asyncio.Event()

    async def make_lookup(key: str) -> str:
        calls[key] = calls.get(key, 0) + 1
        await release.wait()
        return key

    sf: SingleFlight[str] = SingleFlight()
    tasks = [
        asyncio.create_task(sf.do("a", lambda: make_lookup("a"))),
        asyncio.create_task(sf.do("b", lambda: make_lookup("b"))),
    ]
    await asyncio.sleep(0)
    assert sf.in_flight_count == 2
    release.set()
    results = await asyncio.gather(*tasks)

    assert sorted(results) == ["a", "b"]
    assert calls == {"a": 1, "b": 1}


@pytest.mark.asyncio
async def test_sequential_calls_each_run_after_completion() -> None:
    """Once a lookup finishes the entry is cleared, so the next call re-runs it."""
    calls = 0

    async def lookup() -> int:
        nonlocal calls
        calls += 1
        return calls

    sf: SingleFlight[int] = SingleFlight()
    assert await sf.do("k", lookup) == 1
    assert await sf.do("k", lookup) == 2
    assert sf.in_flight_count == 0


@pytest.mark.asyncio
async def test_in_flight_map_is_bounded() -> None:
    """A new key beyond max_in_flight skips coalescing rather than growing the map."""
    release = asyncio.Event()

    async def blocker() -> str:
        await release.wait()
        return "v"

    sf: SingleFlight[str] = SingleFlight(max_in_flight=2)
    held = [
        asyncio.create_task(sf.do("a", blocker)),
        asyncio.create_task(sf.do("b", blocker)),
    ]
    await asyncio.sleep(0)
    assert sf.in_flight_count == 2

    # Third distinct key: map is full, so it runs uncoalesced (not tracked).
    ran = False

    async def uncoalesced() -> str:
        nonlocal ran
        ran = True
        return "c"

    assert await sf.do("c", uncoalesced) == "c"
    assert ran is True
    assert sf.in_flight_count == 2  # unchanged — "c" was never installed

    release.set()
    await asyncio.gather(*held)
    assert sf.in_flight_count == 0


@pytest.mark.asyncio
async def test_cancelled_waiter_leaves_shared_lookup_intact() -> None:
    """Cancelling one waiter neither cancels the shared Future nor breaks the leader."""
    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def lookup() -> str:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return "value"

    sf: SingleFlight[str] = SingleFlight()
    tasks = [asyncio.create_task(sf.do("k", lookup)) for _ in range(5)]
    await started.wait()
    await asyncio.sleep(0)
    tasks[2].cancel()
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert tasks[2].cancelled()
    assert [r for i, r in enumerate(results) if i != 2] == ["value"] * 4
    assert calls == 1
    assert sf.in_flight_count == 0


@pytest.mark.asyncio
async def test_cancelled_leader_hands_off_to_a_waiter() -> None:
    """A cancelled leader aborts only its own lookup; one waiter re-runs it for the rest."""
    calls = 0
    release = asyncio.Event()
    first_started = asyncio.Event()

    async def lookup() -> str:
        nonlocal calls
        calls += 1
        first_started.set()
        await release.wait()
        return "value"

    sf: SingleFlight[str] = SingleFlight()
    tasks = [asyncio.create_task(sf.do("k", lookup)) for _ in range(5)]
    await first_started.wait()
    await asyncio.sleep(0)
    tasks[0].cancel()
    for _ in range(3):
        await asyncio.sleep(0)
    # The hand-off re-installed exactly one flight for the surviving waiters.
    assert sf.in_flight_count == 1
    release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert tasks[0].cancelled()
    assert results[1:] == ["value"] * 4
    assert calls == 2
    assert sf.in_flight_count == 0


@pytest.mark.asyncio
async def test_cancelled_leader_without_waiters_pins_nothing() -> None:
    """A lone cancelled leader leaves no entry behind; the next call runs fresh."""
    started = asyncio.Event()

    async def hang() -> str:
        started.set()
        await asyncio.Event().wait()
        return "never"

    sf: SingleFlight[str] = SingleFlight()
    task = asyncio.create_task(sf.do("k", hang))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sf.in_flight_count == 0

    async def ok() -> str:
        return "fresh"

    assert await sf.do("k", ok) == "fresh"


def test_rejects_invalid_max_in_flight() -> None:
    with pytest.raises(ValueError, match="max_in_flight"):
        SingleFlight(max_in_flight=0)
