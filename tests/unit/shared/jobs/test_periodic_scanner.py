"""Unit tests for the PeriodicScanner base class and scanner cancellation."""

from __future__ import annotations

import asyncio
from typing import ClassVar
from unittest.mock import MagicMock

import pytest
import structlog

from jentic_one.shared.jobs.catalog_update_scanner import CatalogUpdateScanner
from jentic_one.shared.jobs.connect_poll_scanner import ConnectPollScanner
from jentic_one.shared.jobs.credential_expiry_scanner import CredentialExpiryScanner
from jentic_one.shared.jobs.periodic import PeriodicScanner


class _SleepingScanner(PeriodicScanner):
    name: ClassVar[str] = "test_sleeping_scanner"

    def __init__(self, poll_interval: float = 0.0) -> None:
        self._poll_interval = poll_interval
        self._running = False
        self.entered = asyncio.Event()
        self._block = asyncio.Event()

    async def _tick(self) -> None:
        self.entered.set()
        await self._block.wait()


class _FlakyScanner(PeriodicScanner):
    name: ClassVar[str] = "test_flaky_scanner"

    def __init__(self) -> None:
        self._poll_interval = 0.0
        self._running = False
        self.ticks = 0

    async def _tick(self) -> None:
        self.ticks += 1
        if self.ticks == 1:
            raise RuntimeError("transient db error")
        self.stop()


class _SingleTickScanner(PeriodicScanner):
    name: ClassVar[str] = "test_single_tick_scanner"

    def __init__(self) -> None:
        self._poll_interval = 0.0
        self._running = False
        self.ticks = 0

    async def _tick(self) -> None:
        self.ticks += 1
        self.stop()


@pytest.mark.asyncio
async def test_cancel_propagates() -> None:
    """Cancelling a running scanner task must propagate CancelledError."""
    scanner = _SleepingScanner()
    with structlog.testing.capture_logs() as logs:
        task = asyncio.create_task(scanner.run())
        await asyncio.wait_for(scanner.entered.wait(), timeout=1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert task.cancelled() is True
    assert scanner._running is False
    events = [entry["event"] for entry in logs]
    assert events == [
        "test_sleeping_scanner_started",
        "test_sleeping_scanner_cancelled",
        "test_sleeping_scanner_stopped",
    ]


@pytest.mark.asyncio
async def test_tick_error_does_not_kill_loop() -> None:
    """A non-cancellation error in _tick is logged and the next tick still runs."""
    scanner = _FlakyScanner()
    with structlog.testing.capture_logs() as logs:
        await asyncio.wait_for(scanner.run(), timeout=1.0)

    assert scanner.ticks == 2
    assert scanner._running is False
    events = [entry["event"] for entry in logs]
    assert "test_flaky_scanner_tick_error" in events
    assert events[0] == "test_flaky_scanner_started"
    assert events[-1] == "test_flaky_scanner_stopped"


@pytest.mark.asyncio
async def test_stop_exits_after_current_tick() -> None:
    """Calling stop() exits the run loop cleanly after the current tick."""
    scanner = _SingleTickScanner()
    with structlog.testing.capture_logs() as logs:
        task = asyncio.create_task(scanner.run())
        await asyncio.wait_for(task, timeout=1.0)

    assert task.cancelled() is False
    assert scanner.ticks == 1
    assert scanner._running is False
    events = [entry["event"] for entry in logs]
    assert events == [
        "test_single_tick_scanner_started",
        "test_single_tick_scanner_stopped",
    ]


def test_concrete_scanners_subclass_periodic_scanner() -> None:
    """All three background scanners inherit from PeriodicScanner."""
    assert issubclass(CredentialExpiryScanner, PeriodicScanner)
    assert issubclass(ConnectPollScanner, PeriodicScanner)
    assert issubclass(CatalogUpdateScanner, PeriodicScanner)

    ctx = MagicMock()
    ctx.config.catalog.update_check_interval_seconds = 0
    catalog_scanner = CatalogUpdateScanner(ctx, tick_seconds=12.5)
    assert catalog_scanner._sleep_seconds() == 12.5
