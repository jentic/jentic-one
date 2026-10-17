"""Unit tests for cross-process provider-registry refresh.

Covers the ``ProviderConfigRefresher`` (burst coalescing, a change during a
rebuild, reconnect flush, retry, lifecycle), the registry rebuild's instance
reuse and deferred close of replaced providers, the ``Context`` refresh
single-flight, and that resolving a provider never reads the database.
"""

from __future__ import annotations

import asyncio

import pytest

from jentic_one.control.services.credentials.providers import (
    PipedreamProvider,
    ProviderRegistry,
    StaticProvider,
)
from jentic_one.shared.config import AppConfig, CredentialsConfig, PipedreamProviderConfig
from jentic_one.shared.context import Context
from jentic_one.shared.invalidation import (
    InProcessInvalidationBus,
    InvalidationHandler,
    InvalidationMessage,
    NoopInvalidationBus,
)
from jentic_one.shared.provider_refresh import PROVIDER_CONFIG_TOPIC, ProviderConfigRefresher


class _CountingRefresh:
    """An async refresh callable that counts calls and can be held open."""

    def __init__(self) -> None:
        self.calls = 0
        self.release = asyncio.Event()
        self.release.set()
        self.entered = asyncio.Event()
        self.fail_times = 0

    async def __call__(self) -> None:
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("admin db unavailable")


def _message(*, flush: bool = False) -> InvalidationMessage:
    return InvalidationMessage(
        topic=PROVIDER_CONFIG_TOPIC, operation="UPDATE", keys={"name": ("pipedream",)}, flush=flush
    )


async def _started(
    refresh: _CountingRefresh,
) -> tuple[InProcessInvalidationBus, ProviderConfigRefresher]:
    bus = InProcessInvalidationBus()
    refresher = ProviderConfigRefresher(refresh, debounce_s=0, retry_base_s=0, retry_max_s=0)
    refresher.subscribe(bus)
    await bus.start()
    await refresher.start()
    # The live bus asks for one reconciling refresh at start; settle it first.
    await refresher.wait_idle()
    refresh.calls = 0
    return bus, refresher


async def test_burst_of_notifications_causes_a_single_refresh() -> None:
    refresh = _CountingRefresh()
    bus, refresher = await _started(refresh)
    try:
        for _ in range(10):
            await bus.publish(_message())
        await refresher.wait_idle()
        assert refresh.calls == 1
    finally:
        await refresher.stop()


async def test_change_during_a_refresh_causes_exactly_one_more() -> None:
    """A rebuild in flight may have read the table before the change committed."""
    refresh = _CountingRefresh()
    bus, refresher = await _started(refresh)
    try:
        refresh.release.clear()
        refresh.entered.clear()
        await bus.publish(_message())
        await refresh.entered.wait()
        for _ in range(5):
            await bus.publish(_message())
        refresh.release.set()
        await refresher.wait_idle()
        assert refresh.calls == 2
    finally:
        await refresher.stop()


async def test_reconnect_flush_refreshes() -> None:
    refresh = _CountingRefresh()
    bus, refresher = await _started(refresh)
    try:
        await bus.simulate_reconnect()
        await refresher.wait_idle()
        assert refresh.calls == 1
    finally:
        await refresher.stop()


async def test_failed_refresh_is_retried_until_it_succeeds() -> None:
    refresh = _CountingRefresh()
    bus, refresher = await _started(refresh)
    try:
        refresh.fail_times = 2
        await bus.publish(_message())
        await refresher.wait_idle()
        assert refresh.calls == 3
    finally:
        await refresher.stop()


async def test_start_reconciles_once_on_a_delivering_bus_only() -> None:
    live = _CountingRefresh()
    refresher = ProviderConfigRefresher(live, debounce_s=0)
    refresher.subscribe(InProcessInvalidationBus())
    await refresher.start()
    await refresher.wait_idle()
    await refresher.stop()
    assert live.calls == 1

    idle = _CountingRefresh()
    refresher = ProviderConfigRefresher(idle, debounce_s=0)
    refresher.subscribe(NoopInvalidationBus())
    await refresher.start()
    await refresher.wait_idle()
    await refresher.stop()
    assert idle.calls == 0


async def test_requests_outside_the_lifespan_are_ignored() -> None:
    refresh = _CountingRefresh()
    refresher = ProviderConfigRefresher(refresh, debounce_s=0)
    refresher.request()  # before start
    await refresher.start()
    await refresher.stop()
    refresher.request()  # after stop
    await refresher.start()  # a stopped refresher does not restart
    await refresher.wait_idle()
    assert refresh.calls == 0


def test_refresher_subscribes_only_the_provider_config_topic() -> None:
    topics: list[str] = []

    class _Bus:
        def subscribe(self, topic: str, handler: InvalidationHandler) -> None:
            topics.append(topic)

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    ProviderConfigRefresher(_CountingRefresh()).subscribe(_Bus())
    assert topics == [PROVIDER_CONFIG_TOPIC] == ["admin.provider_configs"]


# --- registry rebuild + deferred close ----------------------------------------


def _pipedream(secret: str) -> dict[str, object]:
    return {
        "kind": "pipedream",
        "project_id": "proj",
        "client_id": "client",
        "client_secret": secret,
        "environment": "production",
    }


def test_rebuild_reuses_unchanged_providers_and_replaces_changed_ones() -> None:
    cfg = CredentialsConfig()
    first = ProviderRegistry.from_config_and_dynamic(cfg, {"pipedream": _pipedream("s1")})
    same = ProviderRegistry.from_config_and_dynamic(
        cfg, {"pipedream": _pipedream("s1")}, previous=first
    )
    assert same.get("pipedream") is first.get("pipedream")
    assert same.get("static") is first.get("static")
    assert first.superseded_by(same) == []

    rotated = ProviderRegistry.from_config_and_dynamic(
        cfg, {"pipedream": _pipedream("s2")}, previous=same
    )
    assert rotated.get("pipedream") is not same.get("pipedream")
    rotated_cfg = rotated.config("pipedream")
    assert isinstance(rotated_cfg, PipedreamProviderConfig)
    assert rotated_cfg.client_secret.get_secret_value() == "s2"  # pragma: allowlist secret
    assert same.superseded_by(rotated) == [same.get("pipedream")]

    removed = ProviderRegistry.from_config_and_dynamic(cfg, {}, previous=rotated)
    assert "pipedream" not in removed.list_all()
    assert rotated.superseded_by(removed) == [rotated.get("pipedream")]


class _ClosingProvider(StaticProvider):
    """A provider owning a resource: records when, relative to the swap, it closed."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx
        self.closed = False
        self.registry_at_close: ProviderRegistry | None = None

    async def aclose(self) -> None:
        self.closed = True
        self.registry_at_close = self._ctx.providers


def _config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "databases": {
                name: {"backend": "sqlite", "path": f"/nonexistent/{name}.db"}
                for name in ("registry", "admin", "control")
            }
        }
    )


def _ctx_without_dbs() -> Context:
    """A Context that may open no database: any DB access raises."""
    return Context(_config(), allowed_dbs=set())


async def test_replaced_provider_is_closed_only_after_swap_and_grace() -> None:
    ctx = _ctx_without_dbs()
    old_provider = _ClosingProvider(ctx)
    old = ProviderRegistry({"vault": old_provider})
    new = ProviderRegistry({"vault": StaticProvider()})
    ctx._providers = old
    ctx._provider_retire_grace_s = 3600.0

    held = ctx.providers.get("vault")  # an in-flight request resolved it
    ctx._swap_providers(new)
    await ctx._close_retired_providers()
    assert ctx.providers is new
    assert held is old_provider and not old_provider.closed, "still in its grace period"

    await ctx.shutdown()
    assert old_provider.closed
    assert old_provider.registry_at_close is new


async def test_retired_provider_closes_at_the_first_rebuild_past_its_grace() -> None:
    ctx = _ctx_without_dbs()
    provider = _ClosingProvider(ctx)
    ctx._providers = ProviderRegistry({"vault": provider})
    ctx._provider_retire_grace_s = 0.0
    replacement = ProviderRegistry({"vault": StaticProvider()})
    ctx._swap_providers(replacement)
    await ctx._close_retired_providers()
    assert provider.closed and provider.registry_at_close is replacement
    assert ctx._retired_providers == []


async def test_shutdown_closes_the_live_registry_providers() -> None:
    ctx = _ctx_without_dbs()
    provider = _ClosingProvider(ctx)
    ctx._providers = ProviderRegistry({"vault": provider})
    await ctx.shutdown()
    assert provider.closed


async def test_failing_close_does_not_stop_the_others() -> None:
    ctx = _ctx_without_dbs()

    class _Broken(StaticProvider):
        async def aclose(self) -> None:
            raise RuntimeError("boom")

    survivor = _ClosingProvider(ctx)
    ctx._providers = ProviderRegistry({"broken": _Broken(), "vault": survivor})
    await ctx.shutdown()
    assert survivor.closed


def test_resolving_a_provider_never_touches_the_database() -> None:
    """The hot path is an in-memory lookup: a Context with no DB access serves it."""
    ctx = _ctx_without_dbs()
    ctx._swap_providers(
        ProviderRegistry.from_config_and_dynamic(
            CredentialsConfig(), {"pipedream": _pipedream("s1")}
        )
    )
    with pytest.raises(RuntimeError, match="not allowed"):
        _ = ctx.admin_db
    assert isinstance(ctx.providers.get("pipedream"), PipedreamProvider)
    assert isinstance(ctx.providers.get("static"), StaticProvider)


# --- Context.refresh_providers single-flight -----------------------------------


class _SlowBuildContext(Context):
    """A Context whose registry build is observable and can be held open."""

    def __init__(self) -> None:
        super().__init__(_config(), allowed_dbs=set())
        self.builds = 0
        self.gate = asyncio.Event()
        self.version = 0

    async def _build_providers(self) -> ProviderRegistry:
        self.builds += 1
        seen = self.version
        await self.gate.wait()
        return ProviderRegistry.from_config_and_dynamic(
            CredentialsConfig(), {"pipedream": _pipedream(f"v{seen}")}, previous=self._providers
        )


def _secret_of(ctx: Context) -> str:
    cfg = ctx.providers.config("pipedream")
    assert isinstance(cfg, PipedreamProviderConfig)
    return cfg.client_secret.get_secret_value()


async def test_concurrent_refreshes_never_swap_in_an_older_read() -> None:
    ctx = _SlowBuildContext()
    first = asyncio.create_task(ctx.refresh_providers())
    await asyncio.sleep(0)  # first build has read version 0 and is held
    ctx.version = 1  # a change commits while it is held
    second = asyncio.create_task(ctx.refresh_providers())
    third = asyncio.create_task(ctx.refresh_providers())
    ctx.gate.set()
    await asyncio.gather(first, second, third)
    # The first build cannot answer the later requests; one more build does.
    assert ctx.builds == 2
    assert _secret_of(ctx) == "v1"
