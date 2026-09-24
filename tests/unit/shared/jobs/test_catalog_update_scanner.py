"""Unit tests for the CatalogUpdateScanner tick/kill-switch/cadence logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.shared.jobs.catalog_update_scanner import CatalogUpdateScanner


def _make_ctx(*, interval: int = 86400, jitter_ratio: float = 0.15) -> MagicMock:
    ctx = MagicMock()
    ctx.config.catalog.update_check_interval_seconds = interval
    ctx.config.catalog.update_sweep_jitter_ratio = jitter_ratio
    return ctx


@pytest.mark.asyncio
async def test_tick_disabled_when_interval_zero() -> None:
    scanner = CatalogUpdateScanner(_make_ctx(interval=0))
    with patch.object(scanner, "sweep", new_callable=AsyncMock) as sweep:
        await scanner._tick()
    sweep.assert_not_called()


@pytest.mark.asyncio
async def test_first_tick_sweeps_then_gated_within_interval() -> None:
    scanner = CatalogUpdateScanner(_make_ctx(interval=86400))
    with patch.object(scanner, "sweep", new_callable=AsyncMock) as sweep:
        await scanner._tick()  # first tick: due
        await scanner._tick()  # within interval: gated
    sweep.assert_awaited_once()


@pytest.mark.asyncio
async def test_tick_sweeps_again_after_interval_elapses() -> None:
    # jitter_ratio=0 → deterministic exact-interval gate for this boundary assertion.
    scanner = CatalogUpdateScanner(_make_ctx(interval=100, jitter_ratio=0.0))
    times = iter([1000.0, 1200.0])

    class _Loop:
        def time(self) -> float:
            return next(times)

    with (
        patch.object(scanner, "sweep", new_callable=AsyncMock) as sweep,
        patch("asyncio.get_running_loop", return_value=_Loop()),
    ):
        await scanner._tick()  # t=1000 → due
        await scanner._tick()  # t=1200 → interval elapsed → due again
    assert sweep.await_count == 2


@pytest.mark.asyncio
async def test_sweep_delegates_to_catalog_service() -> None:
    scanner = CatalogUpdateScanner(_make_ctx())
    fake_service = MagicMock()
    fake_service.run_update_sweep = AsyncMock()
    with patch(
        "jentic_one.shared.jobs.catalog_update_scanner.CatalogService",
        return_value=fake_service,
    ) as ctor:
        await scanner.sweep()
    ctor.assert_called_once_with(scanner._ctx)
    fake_service.run_update_sweep.assert_awaited_once()


def test_stop_flips_running() -> None:
    scanner = CatalogUpdateScanner(_make_ctx())
    scanner._running = True
    scanner.stop()
    assert scanner._running is False


def test_compute_due_interval_disabled_returns_base() -> None:
    # jitter_ratio <= 0 must yield exactly the base interval (deterministic).
    scanner = CatalogUpdateScanner(_make_ctx(interval=100, jitter_ratio=0.0))
    assert scanner._compute_due_interval(100) == 100.0


def test_compute_due_interval_jitters_within_bounds() -> None:
    # With ratio r, the due interval is in [interval, interval*(1+r)] — never below
    # the base (so we never sweep early) and never above the bounded ceiling.
    scanner = CatalogUpdateScanner(_make_ctx(interval=1000, jitter_ratio=0.15))
    samples = [scanner._compute_due_interval(1000) for _ in range(200)]
    assert all(1000.0 <= s <= 1150.0 for s in samples)
    # Not a constant: jitter actually varies the gate (herd-spread is real).
    assert len(set(samples)) > 1


@pytest.mark.asyncio
async def test_next_cycle_gate_uses_jittered_interval() -> None:
    """After a sweep, the next-cycle gate is the jittered interval, not the raw one.

    Pin the jitter draw so the boundary is deterministic: with the drawn due interval
    at 1150, an elapsed of 1100 (< 1150) must NOT re-sweep, proving the gate widened
    past the raw 1000s interval by the jitter.
    """
    scanner = CatalogUpdateScanner(_make_ctx(interval=1000, jitter_ratio=0.15))
    times = iter([0.0, 1100.0])

    class _Loop:
        def time(self) -> float:
            return next(times)

    with (
        patch.object(scanner, "sweep", new_callable=AsyncMock) as sweep,
        patch("asyncio.get_running_loop", return_value=_Loop()),
        # Draw the max jitter → due interval = 1000 * (1 + 0.15) = 1150.
        patch(
            "jentic_one.shared.jobs.catalog_update_scanner.random.uniform",
            return_value=0.15,
        ),
    ):
        await scanner._tick()  # t=0 → first sweep, gate for next cycle = 1150
        await scanner._tick()  # t=1100 → 1100 < 1150 → still gated, no re-sweep
    sweep.assert_awaited_once()
    assert scanner._due_interval == 1150.0


@pytest.mark.asyncio
async def test_first_tick_sweeps_immediately_with_jitter_enabled() -> None:
    """Jitter must not gate the very first tick — it sweeps immediately, then jitters.

    The first-tick path short-circuits on ``_last_swept_at is None`` before the gate
    comparison, so an enabled jitter ratio can't delay the initial sweep. After it, the
    next-cycle gate is a jittered value strictly above the raw interval (max draw).
    """
    scanner = CatalogUpdateScanner(_make_ctx(interval=1000, jitter_ratio=0.15))
    with (
        patch.object(scanner, "sweep", new_callable=AsyncMock) as sweep,
        patch(
            "jentic_one.shared.jobs.catalog_update_scanner.random.uniform",
            return_value=0.15,
        ),
    ):
        await scanner._tick()
    sweep.assert_awaited_once()
    assert scanner._due_interval == 1150.0


@pytest.mark.asyncio
async def test_lowered_interval_clamps_stale_gate() -> None:
    """A mid-run drop in the interval must not leave the scanner on the old, larger gate.

    Simulate config being lowered after a sweep: the stale ``_due_interval`` (from the
    old 1000s interval → 1150) must be clamped to the *new* interval's jitter ceiling
    (100 * 1.15 = 115), so an elapsed of 120 re-sweeps instead of waiting out the stale
    gate. Guards the correctness-lens stale-gate finding.
    """
    scanner = CatalogUpdateScanner(_make_ctx(interval=1000, jitter_ratio=0.15))
    scanner._last_swept_at = 0.0
    scanner._due_interval = 1150.0  # gate frozen from the old large interval
    scanner._cfg.update_check_interval_seconds = 100  # operator lowered it

    times = iter([120.0])

    class _Loop:
        def time(self) -> float:
            return next(times)

    with (
        patch.object(scanner, "sweep", new_callable=AsyncMock) as sweep,
        patch("asyncio.get_running_loop", return_value=_Loop()),
        patch(
            "jentic_one.shared.jobs.catalog_update_scanner.random.uniform",
            return_value=0.0,
        ),
    ):
        await scanner._tick()  # elapsed 120 > clamped ceiling 115 → sweep
    sweep.assert_awaited_once()


def test_max_due_interval_is_current_ceiling() -> None:
    scanner = CatalogUpdateScanner(_make_ctx(interval=200, jitter_ratio=0.25))
    assert scanner._max_due_interval(200) == 250.0
    # A negative ratio is floored at 0 → ceiling is exactly the interval.
    scanner._cfg.update_sweep_jitter_ratio = -1.0
    assert scanner._max_due_interval(200) == 200.0
