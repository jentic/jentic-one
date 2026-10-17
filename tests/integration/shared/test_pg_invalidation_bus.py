"""PostgreSQL integration tests for cross-process cache invalidation.

Proves, against the real database:

- the ``PgListenInvalidationBus`` meets ``BaseInvalidationBusContract`` (real
  ``pg_notify`` delivery, reconnect after ``pg_terminate_backend``);
- the admin/control triggers emit the documented payloads, only on commit;
- the issue's acceptance bar: a token / API-key revoke committed through one
  process's engines is seen by a separately-built broker's caches — wired by
  the production ``install_broker_auth`` — long before the cache TTL expires.

The "two processes" are two independent ``Context``s: separate engines and
pools, and a separate LISTEN connection, sharing only the database.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import secrets
import time
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from jentic_one.broker.core.setup import install_broker_auth
from jentic_one.shared.auth.errors import TokenValidationError
from jentic_one.shared.auth.permission_catalog import BROKER_EXECUTE_PERMISSION
from jentic_one.shared.config import AppConfig, InvalidationConfig
from jentic_one.shared.context import Context
from jentic_one.shared.invalidation import (
    PG_INVALIDATION_CHANNEL,
    InvalidationBus,
    InvalidationMessage,
    PgListenInvalidationBus,
    build_invalidation_bus,
    build_trigger_payload,
    listen_targets,
)
from jentic_one.testing import BaseInvalidationBusContract

pytestmark = pytest.mark.integration

#: A TTL far longer than any test runs: an entry that disappears did so
#: because of the bus, never because it expired.
_LONG_TTL_S = 600.0
#: Generous bound on NOTIFY → handler latency on a loaded CI runner.
_PROPAGATION_S = 5.0

_FAST_RECONNECT = InvalidationConfig(
    reconnect_backoff_base_s=0.05, reconnect_backoff_max_s=0.2, health_check_interval_s=1.0
)


@pytest.fixture(autouse=True)
def _postgres_only(integration_config: AppConfig) -> None:
    if integration_config.databases.admin.backend != "postgres":
        pytest.skip("LISTEN/NOTIFY is PostgreSQL-only; SQLite runs the no-op bus")


def _raw_conn_kwargs(config: AppConfig) -> dict[str, object]:
    admin = config.databases.admin
    return {
        "host": admin.host,
        "port": admin.port,
        "user": admin.user,
        "password": admin.password.get_secret_value(),
        "database": admin.name,
    }


def _pg_bus(config: AppConfig) -> PgListenInvalidationBus:
    dbs = {"admin": config.databases.admin, "control": config.databases.control}
    return PgListenInvalidationBus(listen_targets(dbs), config=_FAST_RECONNECT)


async def _eventually(condition: Callable[[], bool], what: str) -> None:
    deadline = time.monotonic() + _PROPAGATION_S
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.02)


def _about(
    messages: list[InvalidationMessage], key: str, operation: str | None = None
) -> list[InvalidationMessage]:
    """The messages that name ``key`` (and ``operation``) — other writers are ignored."""
    return [
        m
        for m in messages
        if (operation is None or m.operation == operation)
        and any(key in values for values in m.keys.values())
    ]


# --- the contract, against a real LISTEN connection ------------------------


class _PgContract(BaseInvalidationBusContract):
    """The contract over a real LISTEN connection, isolated from real writes.

    The bus maps a private, per-test schema name to ``admin``, and the contract
    publishes under that name: trigger traffic from anything else writing to
    the shared fixture database (other suites, other processes) is ignored by
    the bus, so the contract's exact-count assertions cannot flake.
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.schema = f"inv_contract_{secrets.token_hex(4)}"

    async def bus_factory(self) -> InvalidationBus:
        admin = self.config.databases.admin.model_copy(update={"schema_name": self.schema})
        return PgListenInvalidationBus(listen_targets({"admin": admin}), config=_FAST_RECONNECT)

    async def publish(
        self, bus: InvalidationBus, topic: str, keys: Mapping[str, Sequence[str]]
    ) -> None:
        _database, _, table = topic.partition(".")
        schema = self.schema
        payload = build_trigger_payload(schema=schema, table=table, operation="UPDATE", keys=keys)
        conn = await asyncpg.connect(**_raw_conn_kwargs(self.config))
        try:
            await conn.execute("SELECT pg_notify($1, $2)", PG_INVALIDATION_CHANNEL, payload)
        finally:
            await conn.close()

    async def force_reconnect(self, bus: InvalidationBus) -> bool:
        assert isinstance(bus, PgListenInvalidationBus)
        [target] = bus.target_names
        pid = bus.listener_pid(target)
        assert pid is not None, "bus is not listening"
        conn = await asyncpg.connect(**_raw_conn_kwargs(self.config))
        try:
            assert await conn.fetchval("SELECT pg_terminate_backend($1)", pid)
        finally:
            await conn.close()
        return True


@pytest.mark.parametrize(
    "name", sorted(n for n in dir(BaseInvalidationBusContract) if n.startswith("test_"))
)
async def test_pg_listen_bus_meets_the_contract(integration_config: AppConfig, name: str) -> None:
    await getattr(_PgContract(integration_config), name)()


# --- triggers ---------------------------------------------------------------


@pytest.fixture()
async def listening(
    integration_config: AppConfig,
) -> AsyncGenerator[list[InvalidationMessage], None]:
    """A started PG bus recording every message on the triggered admin/control topics."""
    bus = _pg_bus(integration_config)
    received: list[InvalidationMessage] = []
    for topic in (
        "admin.agents",
        "admin.access_tokens",
        "control.permission_rule_set_rules",
    ):
        bus.subscribe(topic, received.append)
    await bus.start()
    try:
        yield received
    finally:
        await bus.stop()


async def test_trigger_notifies_on_commit_with_old_and_new_keys(
    integration_context: Context, listening: list[InvalidationMessage]
) -> None:
    agent_id = f"agnt_inv{secrets.token_hex(6)}"
    renamed = f"{agent_id}x"
    try:
        async with integration_context.admin_db.transaction() as session:
            await session.execute(
                text(
                    "INSERT INTO agents (id, name, registered_by, status, created_by) "
                    "VALUES (:id, 'inv', 'system:test', 'active', 'system:test')"
                ),
                {"id": agent_id},
            )
        await _eventually(lambda: bool(_about(listening, agent_id, "INSERT")), "INSERT notify")
        [insert] = _about(listening, agent_id, "INSERT")
        assert insert.topic == "admin.agents"
        assert insert.values("id") == (agent_id,)

        async with integration_context.admin_db.transaction() as session:
            await session.execute(
                text("UPDATE agents SET id = :new WHERE id = :old"),
                {"new": renamed, "old": agent_id},
            )
        await _eventually(lambda: bool(_about(listening, renamed, "UPDATE")), "UPDATE notify")
        [update] = _about(listening, renamed, "UPDATE")
        assert set(update.values("id")) == {agent_id, renamed}
    finally:
        async with integration_context.admin_db.transaction() as session:
            await session.execute(
                text("DELETE FROM agents WHERE id IN (:a, :b)"), {"a": agent_id, "b": renamed}
            )


async def test_trigger_is_silent_on_rollback_and_no_op_update(
    integration_context: Context, listening: list[InvalidationMessage]
) -> None:
    agent_id = f"agnt_inv{secrets.token_hex(6)}"
    try:
        async with integration_context.admin_db.transaction() as session:
            await session.execute(
                text(
                    "INSERT INTO agents (id, name, registered_by, status, created_by) "
                    "VALUES (:id, 'inv', 'system:test', 'active', 'system:test')"
                ),
                {"id": agent_id},
            )
        await _eventually(lambda: bool(_about(listening, agent_id, "INSERT")), "INSERT notify")

        with pytest.raises(RuntimeError):
            async with integration_context.admin_db.transaction() as session:
                await session.execute(
                    text("UPDATE agents SET status = 'disabled' WHERE id = :id"), {"id": agent_id}
                )
                raise RuntimeError("roll back")
        async with integration_context.admin_db.transaction() as session:
            await session.execute(
                text("UPDATE agents SET status = status WHERE id = :id"), {"id": agent_id}
            )
            # Bookkeeping-only: the trigger ignores updated_at/last_used_at.
            await session.execute(
                text("UPDATE agents SET updated_at = now() WHERE id = :id"), {"id": agent_id}
            )
        # A marker change proves the listener is live, so silence above is real.
        async with integration_context.control_db.transaction() as session:
            await session.execute(
                text("SELECT pg_notify(:c, :p)"),
                {
                    "c": PG_INVALIDATION_CHANNEL,
                    "p": build_trigger_payload(
                        schema=integration_context.config.databases.control.schema_name,
                        table="permission_rule_set_rules",
                        operation="MARKER",
                    ),
                },
            )
        await _eventually(lambda: any(m.operation == "MARKER" for m in listening), "marker")
        assert [m.operation for m in _about(listening, agent_id)] == ["INSERT"]
    finally:
        async with integration_context.admin_db.transaction() as session:
            await session.execute(text("DELETE FROM agents WHERE id = :id"), {"id": agent_id})


@pytest.mark.parametrize(
    ("database", "migration_module"),
    [
        (
            "admin",
            "jentic_one.migrations.admin.versions.e046c1696c4b_add_cache_invalidation_triggers",
        ),
        (
            "control",
            "jentic_one.migrations.control.versions.5a78aa331015_add_cache_invalidation_triggers",
        ),
    ],
)
async def test_every_triggered_table_carries_both_triggers(
    integration_context: Context, database: str, migration_module: str
) -> None:
    """The migrated schema has the row and TRUNCATE trigger on every listed table."""
    migration = importlib.import_module(migration_module)
    schema = getattr(integration_context.config.databases, database).schema_name
    db = getattr(integration_context, f"{database}_db")
    async with db.session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT c.relname, t.tgname FROM pg_trigger t"
                    " JOIN pg_class c ON c.oid = t.tgrelid"
                    " JOIN pg_namespace n ON n.oid = c.relnamespace"
                    " WHERE n.nspname = :schema AND t.tgname LIKE '%_invalidation_%'"
                ),
                {"schema": schema},
            )
        ).all()
    found = {(r.relname, r.tgname) for r in rows}
    expected = {
        (table, f"{table}_invalidation_{kind}")
        for table in migration.TRIGGERED_TABLES
        for kind in ("row", "truncate")
    }
    assert found == expected


# --- acceptance: a revoke reaches a separate broker before the TTL ----------


_OWNER = "usr_inv_owner"


@pytest.fixture()
async def two_processes(
    integration_config: AppConfig,
) -> AsyncGenerator[tuple[Context, FastAPI], None]:
    """(writer context, broker app) — independent engines, one shared database."""
    broker_config = integration_config.model_copy(
        update={
            "broker": integration_config.broker.model_copy(
                update={"resolve_cache_ttl_seconds": _LONG_TTL_S}
            ),
            "invalidation": _FAST_RECONNECT,
        }
    )
    async with (
        Context(integration_config) as writer,
        Context(broker_config, allowed_dbs={"registry", "admin", "control"}) as broker_ctx,
    ):
        app = FastAPI()
        app.state.invalidation_bus = build_invalidation_bus(broker_ctx)
        assert isinstance(app.state.invalidation_bus, PgListenInvalidationBus)
        install_broker_auth(app, broker_ctx)
        await app.state.invalidation_bus.start()
        try:
            yield writer, app
        finally:
            await app.state.invalidation_bus.stop()
            async with writer.admin_db.transaction() as session:
                for stmt in (
                    "DELETE FROM access_tokens WHERE actor_id LIKE 'agnt_inv%'",
                    "DELETE FROM actor_permission_grants WHERE actor_id LIKE 'agnt_inv%'",
                    "DELETE FROM agent_credentials WHERE agent_id LIKE 'agnt_inv%'",
                    "DELETE FROM agents WHERE id LIKE 'agnt_inv%'",
                    f"DELETE FROM users WHERE id = '{_OWNER}'",
                ):
                    await session.execute(text(stmt))


async def _seed_agent_with_credentials(writer: Context) -> tuple[str, str, str]:
    """An active agent with an opaque ``at_`` token and a ``jak_`` key."""
    agent_id = f"agnt_inv{secrets.token_hex(6)}"
    access_token = f"at_{secrets.token_hex(16)}"
    api_key = f"jak_{secrets.token_hex(16)}"
    async with writer.admin_db.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, email, first_name, last_name) "
                "VALUES (:id, 'inv-owner@test.local', 'I', 'O') ON CONFLICT DO NOTHING"
            ),
            {"id": _OWNER},
        )
        await session.execute(
            text(
                "INSERT INTO agents (id, name, owner_id, registered_by, status, created_by) "
                "VALUES (:id, 'inv', :owner, 'system:test', 'active', 'system:test')"
            ),
            {"id": agent_id, "owner": _OWNER},
        )
        await session.execute(
            text(
                "INSERT INTO agent_credentials (id, agent_id, api_key_hash, created_by) "
                "VALUES (:id, :agent, :hash, 'system:test')"
            ),
            {
                "id": f"agc_{agent_id}",
                "agent": agent_id,
                "hash": hashlib.sha256(api_key.encode()).hexdigest(),
            },
        )
        await session.execute(
            text(
                "INSERT INTO actor_permission_grants (id, actor_id, actor_type, permission, "
                "created_by) VALUES (:id, :actor, 'agent', :perm, 'system:test')"
            ),
            {"id": f"asg_{agent_id}", "actor": agent_id, "perm": BROKER_EXECUTE_PERMISSION},
        )
        await session.execute(
            text(
                "INSERT INTO access_tokens (token_hash, actor_id, actor_type, scopes, "
                "token_family_id, expires_at, created_by, is_ephemeral) VALUES "
                "(:hash, :actor, 'agent', CAST(:scopes AS jsonb), 'fam_inv', :exp, :actor, true)"
            ),
            {
                "hash": hashlib.sha256(access_token.encode()).hexdigest(),
                "actor": agent_id,
                "scopes": f'["{BROKER_EXECUTE_PERMISSION}"]',
                "exp": datetime.now(UTC) + timedelta(hours=1),
            },
        )
    return agent_id, access_token, api_key


async def _rejected_within_propagation(app: FastAPI, credential: str) -> float:
    """Seconds until the broker rejects ``credential``; fails past the bound."""
    validator = app.state.broker_token_validator
    started = time.monotonic()
    while True:
        try:
            await validator.validate(credential)
        except TokenValidationError:
            return time.monotonic() - started
        if time.monotonic() - started > _PROPAGATION_S:
            raise AssertionError(
                f"broker still accepts the revoked credential after {_PROPAGATION_S}s "
                f"(cache TTL is {_LONG_TTL_S}s)"
            )
        await asyncio.sleep(0.02)


async def test_token_revoke_reaches_another_broker_before_ttl(
    two_processes: tuple[Context, FastAPI],
) -> None:
    writer, app = two_processes
    _agent, access_token, _key = await _seed_agent_with_credentials(writer)
    validator = app.state.broker_token_validator
    assert (await validator.validate(access_token)).active  # now cached for _LONG_TTL_S

    async with writer.admin_db.transaction() as session:
        await session.execute(
            text("UPDATE access_tokens SET revoked_at = now() WHERE token_hash = :h"),
            {"h": hashlib.sha256(access_token.encode()).hexdigest()},
        )

    assert await _rejected_within_propagation(app, access_token) < _LONG_TTL_S


async def test_api_key_revoke_reaches_another_broker_before_ttl(
    two_processes: tuple[Context, FastAPI],
) -> None:
    writer, app = two_processes
    agent_id, _token, api_key = await _seed_agent_with_credentials(writer)
    validator = app.state.broker_token_validator
    assert (await validator.validate(api_key)).sub == agent_id

    async with writer.admin_db.transaction() as session:
        await session.execute(
            text("DELETE FROM agent_credentials WHERE agent_id = :a"), {"a": agent_id}
        )

    assert await _rejected_within_propagation(app, api_key) < _LONG_TTL_S


async def test_agent_disable_reaches_another_broker_before_ttl(
    two_processes: tuple[Context, FastAPI],
) -> None:
    writer, app = two_processes
    agent_id, access_token, api_key = await _seed_agent_with_credentials(writer)
    validator = app.state.broker_token_validator
    await validator.validate(access_token)
    await validator.validate(api_key)

    async with writer.admin_db.transaction() as session:
        await session.execute(
            text("UPDATE agents SET status = 'disabled' WHERE id = :a"), {"a": agent_id}
        )

    await _rejected_within_propagation(app, access_token)
    await _rejected_within_propagation(app, api_key)
