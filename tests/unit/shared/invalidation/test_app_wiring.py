"""Unit tests for the ``InvalidationBus`` seam in the app factory and container."""

from __future__ import annotations

import contextlib
from pathlib import Path

from jentic_one.broker.core.cache_invalidation import BROKER_TOPICS
from jentic_one.broker.web.app import create_app as create_broker_app
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.invalidation import (
    InvalidationHandler,
    NoopInvalidationBus,
)
from jentic_one.shared.web.app_factory import _enter_invalidation_bus, create_combined_app
from jentic_one.shared.web.container import AppContainer


class _RecordingBus:
    """An injected bus that records its lifecycle and subscriptions."""

    def __init__(self) -> None:
        self.topics: list[str] = []
        self.events: list[str] = []

    def subscribe(self, topic: str, handler: InvalidationHandler) -> None:
        self.topics.append(topic)

    async def start(self) -> None:
        self.events.append("start")

    async def stop(self) -> None:
        self.events.append("stop")


def _sqlite_ctx(tmp_path: Path) -> Context:
    config = AppConfig.model_validate(
        {
            "databases": {
                name: {"backend": "sqlite", "path": str(tmp_path / f"{name}.db")}
                for name in ("registry", "admin", "control")
            }
        }
    )
    return Context(config)


def test_broker_caches_subscribe_to_the_injected_bus(tmp_path: Path) -> None:
    bus = _RecordingBus()
    ctx = _sqlite_ctx(tmp_path)
    app = create_broker_app(ctx, AppContainer(ctx=ctx, invalidation_bus=bus))
    assert app.state.invalidation_bus is bus
    assert set(bus.topics) == BROKER_TOPICS
    assert bus.events == [], "the bus starts in the lifespan, not at build time"


def test_default_bus_on_sqlite_is_noop(tmp_path: Path) -> None:
    ctx = _sqlite_ctx(tmp_path)
    app = create_combined_app(ctx, ["registry", "admin", "control", "auth"])
    assert isinstance(app.state.invalidation_bus, NoopInvalidationBus)


async def test_lifespan_entry_starts_and_schedules_stop() -> None:
    bus = _RecordingBus()
    async with contextlib.AsyncExitStack() as stack:
        await _enter_invalidation_bus(stack, bus)
        assert bus.events == ["start"]
    assert bus.events == ["start", "stop"]
