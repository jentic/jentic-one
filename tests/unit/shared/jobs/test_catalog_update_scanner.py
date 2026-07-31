"""Unit tests for the CatalogUpdateScanner tick/kill-switch/cadence logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.shared.jobs.catalog_update_scanner import CatalogUpdateScanner


def _make_ctx(*, interval: int = 86400) -> MagicMock:
    ctx = MagicMock()
    ctx.config.catalog.update_check_interval_seconds = interval
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
    scanner = CatalogUpdateScanner(_make_ctx(interval=100))
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
