"""PostgreSQL integration test: provider config changes reach other processes.

Two independent ``Context``s — separate engines, pools and LISTEN connection,
sharing only the database — stand in for an admin process and a control (or
broker) process. A provider config written through the admin process's real
``ProviderConfigService`` must be reflected in the other process's provider
registry without a restart, via the ``provider_configs`` trigger and the
production subscription wiring. A third process built the same way but not
subscribed is the control: it keeps the config it booted with, so the
propagation is the subscription's doing.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator, Callable

import asyncpg
import pytest
from sqlalchemy import text

from jentic_one.admin.services.provider_config_service import ProviderConfigService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AppConfig, InvalidationConfig, PipedreamProviderConfig
from jentic_one.shared.context import Context
from jentic_one.shared.invalidation import (
    PG_INVALIDATION_CHANNEL,
    PgListenInvalidationBus,
    build_invalidation_bus,
)
from jentic_one.shared.provider_refresh import ProviderConfigRefresher
from jentic_one.shared.web.app_factory import _subscribe_provider_refresh

pytestmark = pytest.mark.integration

#: Generous bound on commit → NOTIFY → rebuild latency on a loaded CI runner.
_PROPAGATION_S = 5.0
_FIRST_SECRET = "pd-first-secret-value"  # pragma: allowlist secret
_ROTATED_SECRET = "pd-rotated-secret-value"  # pragma: allowlist secret
_FAST_RECONNECT = InvalidationConfig(
    reconnect_backoff_base_s=0.05, reconnect_backoff_max_s=0.2, health_check_interval_s=1.0
)


@pytest.fixture(autouse=True)
def _postgres_only(integration_config: AppConfig) -> None:
    if integration_config.databases.admin.backend != "postgres":
        pytest.skip("LISTEN/NOTIFY is PostgreSQL-only; SQLite runs the no-op bus")


def _identity() -> Identity:
    return Identity(sub="usr_test_operator", email="operator@test.local")


def _fields(secret: str, client_id: str = "client_test") -> dict[str, object]:
    return {
        "project_id": "proj_test",
        "client_id": client_id,
        "environment": "production",
        "client_secret": secret,
    }


def _secret_in(ctx: Context) -> str | None:
    cfg = ctx.providers.config("pipedream")
    if not isinstance(cfg, PipedreamProviderConfig):
        return None
    return cfg.client_secret.get_secret_value()


async def _eventually(condition: Callable[[], bool], what: str) -> float:
    started = time.monotonic()
    while not condition():
        if time.monotonic() - started > _PROPAGATION_S:
            raise AssertionError(f"timed out after {_PROPAGATION_S}s waiting for {what}")
        await asyncio.sleep(0.02)
    return time.monotonic() - started


async def _wipe(ctx: Context) -> None:
    async with ctx.admin_db.transaction() as session:
        await session.execute(text("DELETE FROM provider_configs"))


@pytest.fixture()
async def processes(
    integration_config: AppConfig,
) -> AsyncGenerator[tuple[Context, Context, Context], None]:
    """(admin writer, subscribed control process, unsubscribed control process)."""
    config = integration_config.model_copy(update={"invalidation": _FAST_RECONNECT})
    async with Context(config, refresh_providers_on_boot=False) as writer:
        await _wipe(writer)
        await ProviderConfigService(writer).set(
            "pipedream", _fields(_FIRST_SECRET), identity=_identity()
        )
        async with (
            Context(config, allowed_dbs={"admin", "control"}) as reader,
            Context(config, allowed_dbs={"admin", "control"}) as unsubscribed,
        ):
            assert _secret_in(reader) == _FIRST_SECRET, "boot refresh merges the DB config"
            assert _secret_in(unsubscribed) == _FIRST_SECRET
            bus = build_invalidation_bus(reader)
            assert isinstance(bus, PgListenInvalidationBus)
            refresher = _subscribe_provider_refresh(reader, bus)
            assert isinstance(refresher, ProviderConfigRefresher)
            await bus.start()
            await refresher.start()
            try:
                await refresher.wait_idle()
                yield writer, reader, unsubscribed
            finally:
                await refresher.stop()
                await bus.stop()
                await _wipe(writer)


async def test_rotated_secret_reaches_another_process_without_restart(
    processes: tuple[Context, Context, Context],
) -> None:
    writer, reader, unsubscribed = processes
    before = reader.providers.get("pipedream")

    await ProviderConfigService(writer).set(
        "pipedream", {"client_secret": _ROTATED_SECRET}, identity=_identity()
    )

    await _eventually(lambda: _secret_in(reader) == _ROTATED_SECRET, "rotated secret")
    assert reader.providers.get("pipedream") is not before
    # The control process: same boot, no subscription, still on the old secret.
    assert _secret_in(unsubscribed) == _FIRST_SECRET


async def test_removed_config_reaches_another_process(
    processes: tuple[Context, Context, Context],
) -> None:
    writer, reader, _unsubscribed = processes
    await _wipe(writer)
    await _eventually(lambda: "pipedream" not in reader.providers.list_all(), "removal")


async def test_unrelated_write_keeps_the_provider_instance(
    processes: tuple[Context, Context, Context],
) -> None:
    """A refresh that changes nothing for a provider reuses its instance."""
    writer, reader, _unsubscribed = processes
    before = reader.providers.get("pipedream")
    rebuilds = reader._provider_refresh_completed
    async with writer.admin_db.transaction() as session:
        # Touches the row (the trigger fires) without changing the config.
        await session.execute(
            text("UPDATE provider_configs SET created_by = 'usr_other' WHERE name = 'pipedream'")
        )
    await _eventually(lambda: reader._provider_refresh_completed > rebuilds, "a rebuild")
    assert reader.providers.get("pipedream") is before


async def test_trigger_payload_names_the_provider_and_carries_no_secret(
    integration_config: AppConfig, processes: tuple[Context, Context, Context]
) -> None:
    writer, _reader, _unsubscribed = processes
    admin = integration_config.databases.admin
    received: list[str] = []
    conn = await asyncpg.connect(
        host=admin.host,
        port=admin.port,
        user=admin.user,
        password=admin.password.get_secret_value(),
        database=admin.name,
    )
    try:
        await conn.add_listener(
            PG_INVALIDATION_CHANNEL, lambda _c, _pid, _ch, payload: received.append(payload)
        )
        await ProviderConfigService(writer).set(
            "pipedream", {"client_secret": _ROTATED_SECRET}, identity=_identity()
        )
        async with writer.admin_db.session() as session:
            stored = (
                await session.execute(
                    text("SELECT config_json FROM provider_configs WHERE name = 'pipedream'")
                )
            ).scalar_one()
        ciphertext = (json.loads(stored) if isinstance(stored, str) else stored)["client_secret"]

        def _ours() -> list[dict[str, object]]:
            return [
                body
                for body in map(json.loads, received)
                if body.get("t") == "provider_configs" and body.get("s") == admin.schema_name
            ]

        await _eventually(lambda: bool(_ours()), "provider_configs notification")
    finally:
        await conn.close()
    [body] = _ours()
    assert body["k"] == {"name": ["pipedream"]}
    for raw in received:
        assert _ROTATED_SECRET not in raw
        assert ciphertext not in raw
