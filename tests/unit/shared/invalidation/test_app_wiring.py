"""Unit tests for the ``InvalidationBus`` seam in the app factory and container."""

from __future__ import annotations

import contextlib
from pathlib import Path

from jentic_one.broker.core.cache_invalidation import BROKER_TOPICS
from jentic_one.broker.web.app import create_app as create_broker_app
from jentic_one.control.services.credentials.providers import ProviderRegistry
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.invalidation import (
    InProcessInvalidationBus,
    InvalidationHandler,
    InvalidationMessage,
    NoopInvalidationBus,
)
from jentic_one.shared.provider_refresh import PROVIDER_CONFIG_TOPIC
from jentic_one.shared.web.app_factory import (
    _enter_invalidation_bus,
    _enter_provider_refresh,
    _subscribe_provider_refresh,
    create_combined_app,
)
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
    assert set(bus.topics) == BROKER_TOPICS | {PROVIDER_CONFIG_TOPIC}
    assert bus.events == [], "the bus starts in the lifespan, not at build time"


def test_provider_resolving_app_subscribes_the_provider_refresh(tmp_path: Path) -> None:
    bus = _RecordingBus()
    ctx = _sqlite_ctx(tmp_path)
    create_combined_app(
        ctx, ["admin", "control"], container=AppContainer(ctx=ctx, invalidation_bus=bus)
    )
    assert bus.topics.count(PROVIDER_CONFIG_TOPIC) == 1


def test_app_that_resolves_no_provider_does_not_subscribe(tmp_path: Path) -> None:
    bus = _RecordingBus()
    config = _sqlite_ctx(tmp_path).config
    ctx = Context(config, refresh_providers_on_boot=False)
    create_combined_app(
        ctx, ["registry", "admin"], container=AppContainer(ctx=ctx, invalidation_bus=bus)
    )
    assert PROVIDER_CONFIG_TOPIC not in bus.topics


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


class _CountingContext(Context):
    """A Context that counts provider refreshes instead of reading the admin DB."""

    def __init__(self, config: AppConfig) -> None:
        super().__init__(config)
        self.refreshes = 0

    async def refresh_providers(self) -> ProviderRegistry:
        self.refreshes += 1
        return self.providers


async def test_wired_refresh_runs_on_notifications_and_reconnect(tmp_path: Path) -> None:
    """The app factory's subscribe + lifespan-entry helpers, without a full lifespan.

    (A full app lifespan instruments the process-global SQLAlchemy tracer, which
    would leak into other tests.)
    """
    bus = InProcessInvalidationBus()
    ctx = _CountingContext(_sqlite_ctx(tmp_path).config)
    refresher = _subscribe_provider_refresh(ctx, bus)
    assert refresher is not None
    async with contextlib.AsyncExitStack() as stack:
        await _enter_invalidation_bus(stack, bus)
        await _enter_provider_refresh(stack, refresher)
        await refresher.wait_idle()  # the reconciling refresh on a delivering bus
        assert ctx.refreshes == 1
        await bus.publish(InvalidationMessage(topic=PROVIDER_CONFIG_TOPIC, operation="UPDATE"))
        await refresher.wait_idle()
        await bus.simulate_reconnect()
        await refresher.wait_idle()
        assert ctx.refreshes == 3
    refresher.request()
    await refresher.wait_idle()
    assert ctx.refreshes == 3, "nothing refreshes once the stack has unwound"
