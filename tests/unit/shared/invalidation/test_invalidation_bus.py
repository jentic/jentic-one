"""Unit tests for the ``InvalidationBus`` payload format, in-memory buses and factory."""

from __future__ import annotations

import importlib
from collections.abc import Mapping, Sequence

import pytest

from jentic_one.broker.core.cache_invalidation import BROKER_TOPICS
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.invalidation import (
    PG_INVALIDATION_CHANNEL,
    InProcessInvalidationBus,
    InvalidationBus,
    InvalidationMessage,
    NoopInvalidationBus,
    PgListenInvalidationBus,
    build_invalidation_bus,
    build_trigger_payload,
    parse_trigger_payload,
    topic_for,
)
from jentic_one.shared.provider_refresh import PROVIDER_CONFIG_TOPIC
from jentic_one.testing import BaseInvalidationBusContract

_ADMIN_MIGRATION = importlib.import_module(
    "jentic_one.migrations.admin.versions.e046c1696c4b_add_cache_invalidation_triggers"
)
_PROVIDER_CONFIG_MIGRATION = importlib.import_module(
    "jentic_one.migrations.admin.versions.b7d41c2e9f60_add_provider_config_invalidation_trigger"
)
_CONTROL_MIGRATION = importlib.import_module(
    "jentic_one.migrations.control.versions.5a78aa331015_add_cache_invalidation_triggers"
)
#: Every admin-tree migration that attaches the admin trigger function.
_ADMIN_TRIGGER_MIGRATIONS = (_ADMIN_MIGRATION, _PROVIDER_CONFIG_MIGRATION)


def test_trigger_payload_round_trips() -> None:
    raw = build_trigger_payload(
        schema="admin", table="access_tokens", operation="UPDATE", keys={"token_hash": ["h1"]}
    )
    decoded = parse_trigger_payload(raw)
    assert decoded is not None
    assert (decoded.schema, decoded.table, decoded.operation) == (
        "admin",
        "access_tokens",
        "UPDATE",
    )
    assert decoded.keys == {"token_hash": ("h1",)}
    assert decoded.flush is False


def test_flush_payload_round_trips() -> None:
    raw = build_trigger_payload(schema="admin", table="agents", operation="TRUNCATE", flush=True)
    decoded = parse_trigger_payload(raw)
    assert decoded is not None and decoded.flush and decoded.keys == {}


@pytest.mark.parametrize(
    "raw",
    ["not json", "[]", '{"v": 2, "s": "admin", "t": "agents"}', '{"v": 1, "t": "agents"}'],
)
def test_malformed_payload_is_rejected(raw: str) -> None:
    assert parse_trigger_payload(raw) is None


def test_migrations_publish_on_the_bus_channel() -> None:
    """The trigger SQL hard-codes the channel; it must match the listener's."""
    for migration in (_ADMIN_MIGRATION, _CONTROL_MIGRATION):
        assert migration.CHANNEL == PG_INVALIDATION_CHANNEL
        assert f"pg_notify('{PG_INVALIDATION_CHANNEL}'" in migration.FUNCTION_SQL


def test_subscribers_cover_exactly_the_triggered_tables() -> None:
    """A subscribed topic with no trigger would never fire; a trigger nobody hears is waste."""
    triggered = {
        topic_for("admin", t) for m in _ADMIN_TRIGGER_MIGRATIONS for t in m.TRIGGERED_TABLES
    } | {topic_for("control", t) for t in _CONTROL_MIGRATION.TRIGGERED_TABLES}
    assert triggered == BROKER_TOPICS | {PROVIDER_CONFIG_TOPIC}


def test_provider_config_trigger_reuses_the_admin_function_and_reports_no_config() -> None:
    """The payload names the provider only; config_json (ciphertext) is never a key."""
    assert _PROVIDER_CONFIG_MIGRATION.down_revision == _ADMIN_MIGRATION.revision
    assert _PROVIDER_CONFIG_MIGRATION.FUNCTION == _ADMIN_MIGRATION.FUNCTION
    assert _PROVIDER_CONFIG_MIGRATION.TRIGGERED_TABLES == {"provider_configs": ("name",)}


async def test_noop_bus_conforms_and_never_delivers() -> None:
    bus = NoopInvalidationBus()
    assert isinstance(bus, InvalidationBus)
    received: list[InvalidationMessage] = []
    bus.subscribe("admin.agents", received.append)
    await bus.start()
    await bus.stop()
    assert received == []


class _InProcessContract(BaseInvalidationBusContract):
    async def bus_factory(self) -> InvalidationBus:
        return InProcessInvalidationBus()

    async def publish(
        self, bus: InvalidationBus, topic: str, keys: Mapping[str, Sequence[str]]
    ) -> None:
        assert isinstance(bus, InProcessInvalidationBus)
        await bus.publish(
            InvalidationMessage(
                topic=topic, operation="UPDATE", keys={k: tuple(v) for k, v in keys.items()}
            )
        )

    async def force_reconnect(self, bus: InvalidationBus) -> bool:
        assert isinstance(bus, InProcessInvalidationBus)
        await bus.simulate_reconnect()
        return True


_CONTRACT_TESTS = sorted(
    name for name in dir(BaseInvalidationBusContract) if name.startswith("test_")
)


@pytest.mark.parametrize("name", _CONTRACT_TESTS)
async def test_in_process_bus_meets_the_contract(name: str) -> None:
    contract = _InProcessContract()
    contract.quiet_period_s = 0.01
    await getattr(contract, name)()


def _config(backend: str, *, enabled: bool = True) -> AppConfig:
    dbs = {
        name: (
            {"backend": "sqlite", "path": f"{name}.db"}
            if backend == "sqlite"
            else {"host": "db", "name": "jentic", "schema_name": name}
        )
        for name in ("registry", "admin", "control")
    }
    return AppConfig.model_validate({"databases": dbs, "invalidation": {"enabled": enabled}})


def test_factory_is_noop_on_sqlite() -> None:
    assert isinstance(build_invalidation_bus(Context(_config("sqlite"))), NoopInvalidationBus)


def test_factory_is_noop_when_disabled() -> None:
    bus = build_invalidation_bus(Context(_config("postgres", enabled=False)))
    assert isinstance(bus, NoopInvalidationBus)


def test_factory_listens_once_per_server_on_postgres() -> None:
    bus = build_invalidation_bus(Context(_config("postgres")))
    assert isinstance(bus, PgListenInvalidationBus)
    # admin and control share one database server → one LISTEN connection.
    assert bus.target_names == ("db:5432/jentic",)


def test_factory_skips_databases_outside_the_gate() -> None:
    bus = build_invalidation_bus(Context(_config("postgres"), allowed_dbs={"registry"}))
    assert isinstance(bus, NoopInvalidationBus)
