"""Scenario tests for the theme-5 Phase 6b drop migrations (gate conditions).

The drop migrations are guard-and-raise: they must refuse to run — loudly,
with operator remediation — when the legacy tables still hold rows and the
Phase-6a runbook has not been completed, and proceed when either the tables
are empty (fresh installs) or the acknowledgement/proxy evidence is present.
These tests drive Alembic programmatically against the real integration
databases, downgrading below the drops and re-upgrading under each gate
condition. (The empty-DB path is exercised implicitly by the session-scoped
``_apply_migrations`` fixture on every run, and the downgrade+re-import drill
lives in ``test_toolkit_export.py``.)
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import AsyncGenerator

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from pydantic import SecretStr
from sqlalchemy import inspect, text

from jentic_one.control.repos.toolkit_flattening_repo import legacy_state_digest
from jentic_one.shared.config import AppConfig
from jentic_one.shared.db.session import DatabaseSession
from tests.integration.conftest import _alembic_config_for

pytestmark = pytest.mark.integration

#: Revisions just below the theme-5 Phase 6b drop migrations. The control one
#: already carries the ack evidence columns (``x5f6a7b8c9d0``).
_CONTROL_PRE_DROP = "x5f6a7b8c9d0"  # pragma: allowlist secret
_ADMIN_PRE_DROP = "c0e1f2a3b4c5"  # pragma: allowlist secret

_CONTROL_LEGACY_TABLES = (
    "toolkit_permission_rules",
    "toolkit_keys",
    "toolkit_credential_bindings",
    "toolkits",
)


def _control_cfg(integration_config: AppConfig) -> AlembicConfig:
    return _alembic_config_for("control", integration_config.databases.control)


def _admin_cfg(integration_config: AppConfig) -> AlembicConfig:
    return _alembic_config_for("admin", integration_config.databases.admin)


async def _table_names(db: DatabaseSession) -> set[str]:
    async with db.session() as session:
        conn = await session.connection()
        return set(await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names()))


@pytest.fixture()
async def restore_control_head(
    integration_config: AppConfig, control_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    """Whatever a test does to the control chain, leave it at head and clean."""
    yield
    async with control_db.session() as session:
        names = await _table_names(control_db)
        for table in (*_CONTROL_LEGACY_TABLES, "toolkit_flattening_acks"):
            if table in names:
                await session.execute(text(f"DELETE FROM {table}"))
        await session.commit()
    await asyncio.to_thread(command.upgrade, _control_cfg(integration_config), "head")


@pytest.fixture()
async def restore_admin_head(
    integration_config: AppConfig, admin_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    """Whatever a test does to the admin chain, leave it at head and clean."""
    yield
    async with admin_db.session() as session:
        names = await _table_names(admin_db)
        if "agent_toolkit_bindings" in names:
            await session.execute(text("DELETE FROM agent_toolkit_bindings"))
        await session.execute(
            text("DELETE FROM agent_credential_bindings WHERE agent_id LIKE 'agnt_6btest%'")
        )
        await session.commit()
    await asyncio.to_thread(command.upgrade, _admin_cfg(integration_config), "head")


async def _insert_ack(
    control_db: DatabaseSession,
    *,
    ack_id: str,
    backfilled: bool,
    control_digest: str | None,
    admin_digest: str | None = None,
) -> None:
    """Write a ``toolkit_flattening_acks`` row as a given tool generation would."""
    acknowledged_at: object = dt.datetime(2026, 9, 11, tzinfo=dt.UTC)
    if control_db.backend.dialect_name == "sqlite":
        acknowledged_at = "2026-09-11 00:00:00.000000"
    async with control_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO toolkit_flattening_acks"
                " (id, acknowledged_at, legacy_pair_count, direct_binding_count,"
                "  report_finding_count, tool_version, created_by,"
                "  execution_names_backfilled, control_state_digest, admin_state_digest)"
                " VALUES (:id, :ts, 1, 1, 0, 'test', 'system:test', :bf, :cd, :ad)"
            ),
            {
                "id": ack_id,
                "ts": acknowledged_at,
                "bf": backfilled,
                "cd": control_digest,
                "ad": admin_digest,
            },
        )
        await session.commit()


_ONE_TOOLKIT_DIGEST = legacy_state_digest({"toolkits": ["tk_6btest_1"]})


async def _seed_one_toolkit(control_db: DatabaseSession) -> None:
    async with control_db.session() as session:
        await session.execute(
            text("INSERT INTO toolkits (id, name) VALUES ('tk_6btest_1', '6b-gate-toolkit')")
        )
        await session.commit()


async def test_control_drop_blocks_rows_without_ack_then_accepts_ack(
    integration_config: AppConfig,
    control_db: DatabaseSession,
    restore_control_head: None,
) -> None:
    """Rows + no ack → raise with the runbook; a qualifying ack → the drop proceeds."""
    cfg = _control_cfg(integration_config)
    await asyncio.to_thread(command.downgrade, cfg, _CONTROL_PRE_DROP)
    await _seed_one_toolkit(control_db)

    with pytest.raises(Exception, match=r"no\s+flattening acknowledgement is on record"):
        await asyncio.to_thread(command.upgrade, cfg, "head")

    # The failed run left the chain below the drop; the tables survive intact.
    names = await _table_names(control_db)
    assert set(_CONTROL_LEGACY_TABLES) <= names

    # Record the acknowledgement (what a Phase-6b `flatten-toolkits --verify
    # --acknowledge` writes) and the same upgrade proceeds — rows and all.
    await _insert_ack(
        control_db, ack_id="tfa_6btest_1", backfilled=True, control_digest=_ONE_TOOLKIT_DIGEST
    )
    await asyncio.to_thread(command.upgrade, cfg, "head")

    names = await _table_names(control_db)
    assert not (set(_CONTROL_LEGACY_TABLES) & names)
    # The sentinel table itself survives the drop (it is the audit trail).
    assert "toolkit_flattening_acks" in names


async def test_control_drop_refuses_a_pre_6b_ack(
    integration_config: AppConfig,
    control_db: DatabaseSession,
    restore_control_head: None,
) -> None:
    """An ack from the previous release never checked the toolkit-name backfill."""
    cfg = _control_cfg(integration_config)
    await asyncio.to_thread(command.downgrade, cfg, _CONTROL_PRE_DROP)
    await _seed_one_toolkit(control_db)
    # The shape x5f6a7b8c9d0 gives an ack written before it existed.
    await _insert_ack(control_db, ack_id="tfa_6btest_old", backfilled=False, control_digest=None)

    with pytest.raises(Exception, match="written by an earlier release"):
        await asyncio.to_thread(command.upgrade, cfg, "head")
    assert set(_CONTROL_LEGACY_TABLES) <= await _table_names(control_db)


async def test_control_drop_refuses_a_stale_ack(
    integration_config: AppConfig,
    control_db: DatabaseSession,
    restore_control_head: None,
) -> None:
    """A toolkit row added after acknowledging (e.g. an old replica) refuses the drop."""
    cfg = _control_cfg(integration_config)
    await asyncio.to_thread(command.downgrade, cfg, _CONTROL_PRE_DROP)
    await _seed_one_toolkit(control_db)
    await _insert_ack(
        control_db, ack_id="tfa_6btest_1", backfilled=True, control_digest=_ONE_TOOLKIT_DIGEST
    )
    async with control_db.session() as session:
        await session.execute(
            text("INSERT INTO toolkits (id, name) VALUES ('tk_6btest_late', '6b-late-toolkit')")
        )
        await session.commit()

    with pytest.raises(Exception, match="toolkit rows changed after"):
        await asyncio.to_thread(command.upgrade, cfg, "head")
    assert set(_CONTROL_LEGACY_TABLES) <= await _table_names(control_db)


async def test_admin_drop_blocks_legacy_rows_without_direct_evidence(
    integration_config: AppConfig,
    admin_db: DatabaseSession,
    restore_admin_head: None,
) -> None:
    """Legacy bindings + zero direct bindings → raise; one direct binding → drop."""
    cfg = _admin_cfg(integration_config)
    await asyncio.to_thread(command.downgrade, cfg, _ADMIN_PRE_DROP)
    async with admin_db.session() as session:
        # Zero direct bindings is the "flattening clearly has not run" state —
        # empty the table (test isolation: other suites clean up after
        # themselves, so anything here is leakage anyway).
        await session.execute(text("DELETE FROM agent_credential_bindings"))
        await session.execute(
            text(
                "INSERT INTO agent_toolkit_bindings (id, agent_id, toolkit_id)"
                " VALUES ('atb_6btest_1', 'agnt_6btest_1', 'tk_6btest_1')"
            )
        )
        await session.commit()

    with pytest.raises(Exception, match="flatten-toolkits"):
        await asyncio.to_thread(command.upgrade, cfg, "head")
    names = await _table_names(admin_db)
    assert "agent_toolkit_bindings" in names

    # A single direct binding is the in-DB evidence the flattening ran.
    async with admin_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO agent_credential_bindings (id, agent_id, credential_id)"
                " VALUES ('acb_6btest_1', 'agnt_6btest_1', 'cred_6btest_1')"
            )
        )
        await session.commit()
    await asyncio.to_thread(command.upgrade, cfg, "head")

    names = await _table_names(admin_db)
    assert "agent_toolkit_bindings" not in names


def _superuser_admin_cfg(integration_config: AppConfig) -> AlembicConfig:
    """Admin chain as a role that can also read the control schema.

    Models a single-role shared-database deployment — the case where the admin
    drop can see ``control.toolkit_flattening_acks`` and must use it instead of
    the direct-binding proxy.
    """
    db = integration_config.databases.admin.model_copy(
        update={"user": "postgres", "password": SecretStr("postgres")}
    )
    return _alembic_config_for("admin", db)


async def test_admin_drop_uses_the_control_ack_when_readable(
    integration_config: AppConfig,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
) -> None:
    """Shared Postgres DB: a direct binding is not enough — the ack must cover the rows."""
    if admin_db.backend.dialect_name == "sqlite":
        pytest.skip("cross-schema ack lookup is PostgreSQL-only")
    cfg = _superuser_admin_cfg(integration_config)
    await asyncio.to_thread(command.downgrade, cfg, _ADMIN_PRE_DROP)
    try:
        async with control_db.session() as session:
            await session.execute(text("DELETE FROM toolkit_flattening_acks"))
            await session.commit()
        async with admin_db.session() as session:
            await session.execute(
                text(
                    "INSERT INTO agent_toolkit_bindings (id, agent_id, toolkit_id)"
                    " VALUES ('atb_6btest_1', 'agnt_6btest_1', 'tk_6btest_1')"
                )
            )
            # The proxy evidence alone would let the drop through.
            await session.execute(
                text(
                    "INSERT INTO agent_credential_bindings (id, agent_id, credential_id)"
                    " VALUES ('acb_6btest_1', 'agnt_6btest_1', 'cred_6btest_1')"
                )
            )
            await session.commit()

        with pytest.raises(Exception, match="holds no acknowledgement written by this release"):
            await asyncio.to_thread(command.upgrade, cfg, "head")

        # An ack covering a different binding set is stale for this one.
        await _insert_ack(
            control_db,
            ack_id="tfa_6btest_stale",
            backfilled=True,
            control_digest="x",
            admin_digest=legacy_state_digest({"agent_toolkit_bindings": ["atb_other"]}),
        )
        with pytest.raises(Exception, match="holds no acknowledgement written by this release"):
            await asyncio.to_thread(command.upgrade, cfg, "head")
        assert "agent_toolkit_bindings" in await _table_names(admin_db)

        await _insert_ack(
            control_db,
            ack_id="tfa_6btest_ok",
            backfilled=True,
            control_digest="x",
            admin_digest=legacy_state_digest({"agent_toolkit_bindings": ["atb_6btest_1"]}),
        )
        await asyncio.to_thread(command.upgrade, cfg, "head")
        assert "agent_toolkit_bindings" not in await _table_names(admin_db)
    finally:
        async with control_db.session() as session:
            await session.execute(text("DELETE FROM toolkit_flattening_acks"))
            await session.commit()
        async with admin_db.session() as session:
            names = await _table_names(admin_db)
            if "agent_toolkit_bindings" in names:
                await session.execute(text("DELETE FROM agent_toolkit_bindings"))
            await session.execute(
                text("DELETE FROM agent_credential_bindings WHERE agent_id LIKE 'agnt_6btest%'")
            )
            await session.commit()
        await asyncio.to_thread(command.upgrade, cfg, "head")
