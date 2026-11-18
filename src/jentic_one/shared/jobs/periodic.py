"""Abstract base class for periodic background scanners."""

from __future__ import annotations

import abc
import asyncio
from typing import ClassVar

import structlog

_logger = structlog.get_logger(__name__)


class PeriodicScanner(abc.ABC):
    """Base loop for periodic background scanners.

    Subclasses set ``name`` (the structured-log event prefix) and implement
    :meth:`_tick`. Non-cancellation errors raised by a single tick are logged
    and the loop continues; ``asyncio.CancelledError`` is logged and re-raised
    so task cancellation is never swallowed.
    """

    name: ClassVar[str]
    _poll_interval: float
    _running: bool = False

    async def run(self) -> None:
        """Run ``_tick`` periodically until stopped or cancelled."""
        self._running = True
        _logger.info(f"{self.name}_started")
        try:
            while self._running:
                try:
                    await self._tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _logger.exception(f"{self.name}_tick_error")
                await asyncio.sleep(self._sleep_seconds())
        except asyncio.CancelledError:
            _logger.info(f"{self.name}_cancelled")
            raise
        finally:
            self._running = False
            _logger.info(f"{self.name}_stopped")

    def stop(self) -> None:
        """Signal the scanner to stop after the current tick."""
        self._running = False

    def _sleep_seconds(self) -> float:
        return self._poll_interval

    @abc.abstractmethod
    async def _tick(self) -> None:
        """Execute one scanner iteration."""
