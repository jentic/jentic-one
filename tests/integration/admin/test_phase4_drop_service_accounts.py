"""Scenario tests for the theme-8 Phase-4 admin drop migration (gate conditions).

``e2f3a4b5c6d7`` is guard-and-raise: it refuses — loudly, with the operator
runbook — while ``service_accounts`` holds rows the Phase-1 job has not
migrated, swept, verified and acknowledged, and it proceeds on a fresh install
or once the latest acknowledgement plus a clean drop-time re-verify are in
place. These tests drive Alembic programmatically against the real integration
databases (Postgres in CI, SQLite via ``JENTIC_TEST_BACKEND=sqlite``),
downgrading below the drop and re-upgrading under each gate condition. The
SQLite-only unit twin (every refusal arm) is
``tests/unit/test_migration_theme8_drop_service_accounts.py``; the empty-DB
path runs on every session through ``_apply_migrations``.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from collections.abc import AsyncGenerator

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import inspect, text

from jentic_one.shared.config import AppConfig
from jentic_one.shared.db.session import DatabaseSession
from tests.integration.conftest import _alembic_config_for

pytestmark = pytest.mark.integration

_ADMIN_PRE_DROP = "d1e2f3a4b5c6"  # pragma: allowlist secret
_SA_TABLES = ("service_accounts", "service_account_credentials")

_OWNER = "usr_p4test_owner"
_AGENT = "agnt_p4test_succ"
_SA = "sva_p4test_1"
_STAMPED_AT = dt.datetime(2026, 9, 1, 10, 0, tzinfo=dt.UTC)
_ACKED_AT = dt.datetime(2026, 9, 2, 10, 0, tzinfo=dt.UTC)


def _admin_cfg(integration_config: AppConfig) -> AlembicConfig:
    return _alembic_config_for("admin", integration_config.databases.admin)


async def _table_names(db: DatabaseSession) -> set[str]:
    async with db.session() as session:
        conn = await session.connection()
        return set(await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names()))


async def _exec(db: DatabaseSession, sql: str, params: dict[str, object] | None = None) -> None:
    async with db.session() as session:
        await session.execute(text(sql), params or {})
        await session.commit()


async def _cleanup(admin_db: DatabaseSession) -> None:
    names = await _table_names(admin_db)
    for table, column in (
        ("actor_scope_grants", "actor_id"),
        ("access_tokens", "actor_id"),
        ("agent_credentials", "agent_id"),
    ):
        await _exec(admin_db, f"DELETE FROM {table} WHERE {column} LIKE '%p4test%'")
    if "service_account_credentials" in names:
        await _exec(admin_db, "DELETE FROM service_account_credentials")
        await _exec(admin_db, "DELETE FROM service_accounts")
    await _exec(admin_db, "DELETE FROM service_account_migration_acks")
    await _exec(admin_db, "DELETE FROM agents WHERE id LIKE '%p4test%'")
    await _exec(admin_db, "DELETE FROM users WHERE id = :id", {"id": _OWNER})


@pytest.fixture()
async def restore_admin_head(
    integration_config: AppConfig, admin_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    """Whatever a test does to the admin chain, leave it at head and clean."""
    yield
    await _cleanup(admin_db)
    await asyncio.to_thread(command.upgrade, _admin_cfg(integration_config), "head")


async def _downgrade(integration_config: AppConfig, admin_db: DatabaseSession) -> None:
    await asyncio.to_thread(command.downgrade, _admin_cfg(integration_config), _ADMIN_PRE_DROP)
    await _cleanup(admin_db)


async def _upgrade(integration_config: AppConfig) -> None:
    await asyncio.to_thread(command.upgrade, _admin_cfg(integration_config), "head")


async def _seed_migrated_and_swept(admin_db: DatabaseSession) -> None:
    """A stamped, archived SA whose digest was NULLed — Phase-1 fully applied."""
    await _exec(
        admin_db,
        "INSERT INTO users (id, email, first_name, last_name)"
        " VALUES (:id, 'p4test@test.local', 'P', 'Four')",
        {"id": _OWNER},
    )
    await _exec(
        admin_db,
        "INSERT INTO agents (id, name, owner_id, registered_by, status, created_by)"
        " VALUES (:id, 'p4-successor', :owner, 'system:test', 'active', 'system:test')",
        {"id": _AGENT, "owner": _OWNER},
    )
    await _exec(
        admin_db,
        "INSERT INTO service_accounts (id, name, owner_id, registered_by, status,"
        " migrated_to_actor_id, migrated_at, created_by)"
        " VALUES (:id, 'legacy', :owner, :owner, 'archived', :succ, :ts, 'system:test')",
        {"id": _SA, "owner": _OWNER, "succ": _AGENT, "ts": _STAMPED_AT},
    )
    await _exec(
        admin_db,
        "INSERT INTO service_account_credentials (id, service_account_id, created_by)"
        " VALUES ('sac_p4test_1', :sa, 'system:test')",
        {"sa": _SA},
    )


async def _insert_ack(
    admin_db: DatabaseSession, *, ack_id: str, acknowledged_at: dt.datetime
) -> None:
    await _exec(
        admin_db,
        "INSERT INTO service_account_migration_acks (id, acknowledged_at, unstamped_count,"
        " grant_twin_missing_count, unrevoked_token_count, digest_mismatch_count,"
        " post_stamp_mutation_count, report_finding_count, tool_version)"
        " VALUES (:id, :ts, 0, 0, 0, 0, 0, 0, 'test')",
        {"id": ack_id, "ts": acknowledged_at},
    )


async def test_drop_refuses_rows_without_ack_then_accepts_ack(
    integration_config: AppConfig, admin_db: DatabaseSession, restore_admin_head: None
) -> None:
    """Migrated rows + no ack → raise with the runbook; an ack → the drop proceeds."""
    await _downgrade(integration_config, admin_db)
    await _seed_migrated_and_swept(admin_db)

    with pytest.raises(Exception, match="no acknowledgement row exists") as exc_info:
        await _upgrade(integration_config)
    assert "migrate-service-accounts --verify" in str(exc_info.value)
    assert set(_SA_TABLES) <= await _table_names(admin_db)

    await _insert_ack(admin_db, ack_id="smak_p4test_1", acknowledged_at=_ACKED_AT)
    await _upgrade(integration_config)

    names = await _table_names(admin_db)
    assert not (set(_SA_TABLES) & names)
    assert "service_account_migration_acks" in names  # the upgrade evidence stays


async def test_drop_reverifies_at_drop_time_despite_an_ack(
    integration_config: AppConfig, admin_db: DatabaseSession, restore_admin_head: None
) -> None:
    """The ack is necessary but not sufficient: a row that regressed after it
    (unswept: still active, SA-keyed grant re-created) refuses the drop."""
    await _downgrade(integration_config, admin_db)
    await _seed_migrated_and_swept(admin_db)
    await _insert_ack(admin_db, ack_id="smak_p4test_1", acknowledged_at=_ACKED_AT)
    await _exec(
        admin_db, "UPDATE service_accounts SET status = 'active' WHERE id = :id", {"id": _SA}
    )
    await _exec(
        admin_db,
        "INSERT INTO actor_scope_grants (id, actor_id, actor_type, scope)"
        " VALUES ('asg_p4test_1', :sa, 'service_account', 'toolkit:read')",
        {"sa": _SA},
    )

    with pytest.raises(Exception, match="stamped but unswept") as exc_info:
        await _upgrade(integration_config)
    assert "post-stamp mutation" in str(exc_info.value)
    assert set(_SA_TABLES) <= await _table_names(admin_db)


async def test_drop_refuses_a_stale_ack(
    integration_config: AppConfig, admin_db: DatabaseSession, restore_admin_head: None
) -> None:
    """A row stamped after the latest acknowledgement refuses (re-acknowledge)."""
    await _downgrade(integration_config, admin_db)
    await _seed_migrated_and_swept(admin_db)
    await _insert_ack(
        admin_db, ack_id="smak_p4test_old", acknowledged_at=_STAMPED_AT - dt.timedelta(days=1)
    )

    with pytest.raises(Exception, match="stamped after the latest acknowledgement"):
        await _upgrade(integration_config)
    assert set(_SA_TABLES) <= await _table_names(admin_db)


async def test_empty_tables_with_a_live_sa_session_refuse(
    integration_config: AppConfig, admin_db: DatabaseSession, restore_admin_head: None
) -> None:
    """Fresh-install path is only for a never-used surface: a live SA session
    token still refuses even with no SA rows."""
    await _downgrade(integration_config, admin_db)
    await _exec(
        admin_db,
        "INSERT INTO access_tokens (id, token_hash, actor_id, actor_type, scopes,"
        " token_family_id, expires_at)"
        " VALUES ('at_p4test_1', 'hash-p4test', :sa, 'service_account', :scopes,"
        " 'fam_p4test', :exp)",
        {
            "sa": _SA,
            "scopes": json.dumps(["toolkit:read"]),
            "exp": dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        },
    )

    with pytest.raises(Exception, match="1 live service-account session token"):
        await _upgrade(integration_config)
    assert set(_SA_TABLES) <= await _table_names(admin_db)


async def test_drop_sweeps_retired_scope_strings(
    integration_config: AppConfig, admin_db: DatabaseSession, restore_admin_head: None
) -> None:
    """The retired ``service-accounts:*`` strings are purged from grant and
    token surfaces; unrelated scopes survive untouched."""
    await _downgrade(integration_config, admin_db)
    await _seed_migrated_and_swept(admin_db)
    await _insert_ack(admin_db, ack_id="smak_p4test_1", acknowledged_at=_ACKED_AT)
    await _exec(
        admin_db,
        "INSERT INTO actor_scope_grants (id, actor_id, actor_type, scope)"
        " VALUES ('asg_p4test_a', :agent, 'agent', 'owner:service-accounts:read'),"
        "        ('asg_p4test_b', :agent, 'agent', 'agents:read')",
        {"agent": _AGENT},
    )
    await _exec(
        admin_db,
        "INSERT INTO access_tokens (id, token_hash, actor_id, actor_type, scopes,"
        " token_family_id, expires_at, revoked_at)"
        " VALUES ('at_p4test_2', 'hash-p4test-2', :agent, 'agent', :scopes,"
        " 'fam_p4test', :exp, NULL)",
        {
            "agent": _AGENT,
            "scopes": json.dumps(["service-accounts:read", "agents:read"]),
            "exp": dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        },
    )

    await _upgrade(integration_config)

    async with admin_db.session() as session:
        grants = {
            row.scope
            for row in (
                await session.execute(
                    text("SELECT scope FROM actor_scope_grants WHERE actor_id = :a"),
                    {"a": _AGENT},
                )
            ).all()
        }
        raw_scopes = (
            await session.execute(text("SELECT scopes FROM access_tokens WHERE id = 'at_p4test_2'"))
        ).scalar_one()
    assert grants == {"agents:read"}
    scopes = json.loads(raw_scopes) if isinstance(raw_scopes, str) else raw_scopes
    assert scopes == ["agents:read"]


async def test_downgrade_recreates_empty_tables(
    integration_config: AppConfig, admin_db: DatabaseSession, restore_admin_head: None
) -> None:
    """The documented rollback shape: both tables back, empty, with the stamp columns."""
    await _downgrade(integration_config, admin_db)
    assert set(_SA_TABLES) <= await _table_names(admin_db)
    async with admin_db.session() as session:
        conn = await session.connection()
        columns = await conn.run_sync(
            lambda sync_conn: {
                c["name"] for c in inspect(sync_conn).get_columns("service_accounts")
            }
        )
        count = (await session.execute(text("SELECT count(*) FROM service_accounts"))).scalar_one()
    assert {"migrated_to_actor_id", "migrated_at"} <= columns
    assert count == 0
