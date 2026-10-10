"""Upgrade/downgrade round trip of the require-approval migrations on a real database.

Runs against the integration databases (PostgreSQL by default, SQLite under
``JENTIC_TEST_BACKEND=sqlite``):

- control ``9f7b048514c6`` sits on main's control head, so the tree has one
  head; downgrading below it narrows ``effect`` back to 10 characters after
  ``70ff607085b4`` rewrote every ``require-approval`` rule to ``deny``, and
  leaves ``allow`` / ``deny`` rules untouched;
- admin ``3306fb9172f1`` drops ``execution_approvals`` on downgrade and
  cancels any ``held`` job first, since nothing can release it afterwards.

Each re-upgrade restores the migrated shape.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from jentic_one.shared.config import AppConfig
from jentic_one.shared.db.session import DatabaseSession
from tests.integration.conftest import _alembic_config_for

pytestmark = pytest.mark.integration

_CONTROL_PARENT = "cc3d4e5f6a7b"  # pragma: allowlist secret
_CONTROL_WIDEN = "9f7b048514c6"  # pragma: allowlist secret
_CONTROL_DATA = "70ff607085b4"  # pragma: allowlist secret
_ADMIN_PARENT = "d2e3f4a5b6c7"  # pragma: allowlist secret
_RULE_TABLES = ("agent_permission_rules", "permission_rule_set_rules")


async def _exec(db: DatabaseSession, sql: str, **params: object) -> None:
    async with db.transaction() as session:
        await session.execute(text(sql), params)


async def _cleanup_control(control_db: DatabaseSession) -> None:
    await _exec(control_db, "DELETE FROM agent_permission_rules WHERE id LIKE 'apr_mrt_%'")
    await _exec(control_db, "DELETE FROM permission_rule_set_rules WHERE id LIKE 'psr_mrt_%'")
    await _exec(control_db, "DELETE FROM permission_rule_sets WHERE id = 'prs_mrt'")
    await _exec(control_db, "DELETE FROM credentials WHERE id = 'cred_mrt'")


@pytest.fixture()
async def restore_heads(
    integration_config: AppConfig, control_db: DatabaseSession, admin_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    await _cleanup_control(control_db)
    await _exec(admin_db, "DELETE FROM jobs WHERE id LIKE 'job_mrt_%'")
    yield
    for name in ("control", "admin"):
        cfg = _alembic_config_for(name, getattr(integration_config.databases, name))
        await asyncio.to_thread(command.upgrade, cfg, "head")
    await _cleanup_control(control_db)
    await _exec(admin_db, "DELETE FROM jobs WHERE id LIKE 'job_mrt_%'")


def test_control_tree_has_one_head_with_the_ask_tier_on_top(integration_config: AppConfig) -> None:
    cfg = _alembic_config_for("control", integration_config.databases.control)
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == [_CONTROL_DATA]
    assert script.get_revision(_CONTROL_DATA).down_revision == _CONTROL_WIDEN
    assert script.get_revision(_CONTROL_WIDEN).down_revision == _CONTROL_PARENT


async def _effect_length(db: DatabaseSession, table: str) -> int | None:
    async with db.session() as session:
        conn = await session.connection()
        cols = await conn.run_sync(lambda sync: inspect(sync).get_columns(table))
    col = next(c for c in cols if c["name"] == "effect")
    return getattr(col["type"], "length", None)


async def _effects(db: DatabaseSession, table: str, prefix: str) -> dict[str, str]:
    async with db.session() as session:
        rows = (
            await session.execute(
                text(f"SELECT id, effect FROM {table} WHERE id LIKE :p"), {"p": f"{prefix}%"}
            )
        ).all()
    return {str(r[0]): str(r[1]) for r in rows}


async def _seed_rules(control_db: DatabaseSession) -> None:
    await _exec(
        control_db,
        "INSERT INTO credentials (id, type, name, api_vendor, state)"
        " VALUES ('cred_mrt', 'API_KEY', 'mrt', 'mrt-vendor', 'active')",
    )
    await _exec(control_db, "INSERT INTO permission_rule_sets (id, name) VALUES ('prs_mrt', 'mrt')")
    for i, effect in enumerate(("allow", "deny", "require-approval")):
        await _exec(
            control_db,
            "INSERT INTO agent_permission_rules"
            " (id, agent_id, credential_id, effect, methods, match_mode, is_system, sequence)"
            " VALUES (:id, 'agnt_mrt', 'cred_mrt', :effect, '[\"GET\"]', 'regex', false, :seq)",
            id=f"apr_mrt_{i}",
            effect=effect,
            seq=i,
        )
        await _exec(
            control_db,
            "INSERT INTO permission_rule_set_rules"
            " (id, rule_set_id, effect, methods, match_mode, is_system, sequence)"
            " VALUES (:id, 'prs_mrt', :effect, '[\"GET\"]', 'regex', false, :seq)",
            id=f"psr_mrt_{i}",
            effect=effect,
            seq=i,
        )


async def test_control_round_trip_rewrites_ask_rules_to_deny(
    integration_config: AppConfig, control_db: DatabaseSession, restore_heads: None
) -> None:
    cfg = _alembic_config_for("control", integration_config.databases.control)
    for table in _RULE_TABLES:
        assert await _effect_length(control_db, table) == 16
    await _seed_rules(control_db)

    await asyncio.to_thread(command.downgrade, cfg, _CONTROL_PARENT)

    for table in _RULE_TABLES:
        assert await _effect_length(control_db, table) == 10
    expected = {"0": "allow", "1": "deny", "2": "deny"}
    apr = await _effects(control_db, "agent_permission_rules", "apr_mrt_")
    psr = await _effects(control_db, "permission_rule_set_rules", "psr_mrt_")
    assert {k.rsplit("_", 1)[1]: v for k, v in apr.items()} == expected
    assert {k.rsplit("_", 1)[1]: v for k, v in psr.items()} == expected

    await asyncio.to_thread(command.upgrade, cfg, "head")
    for table in _RULE_TABLES:
        assert await _effect_length(control_db, table) == 16
    await _exec(
        control_db,
        "UPDATE agent_permission_rules SET effect = 'require-approval' WHERE id = 'apr_mrt_2'",
    )
    # A fresh statement text: asyncpg's cached plans predate the column change.
    async with control_db.session() as session:
        effect = (
            await session.execute(
                text("SELECT effect AS e FROM agent_permission_rules WHERE id = 'apr_mrt_2'")
            )
        ).scalar_one()
    assert effect == "require-approval"


async def _table_names(db: DatabaseSession) -> set[str]:
    async with db.session() as session:
        conn = await session.connection()
        return set(await conn.run_sync(lambda sync: inspect(sync).get_table_names()))


async def test_admin_round_trip_cancels_held_jobs(
    integration_config: AppConfig, admin_db: DatabaseSession, restore_heads: None
) -> None:
    cfg = _alembic_config_for("admin", integration_config.databases.admin)
    await _exec(
        admin_db,
        "INSERT INTO jobs (id, kind, status, actor_type, created_by)"
        " VALUES ('job_mrt_held', 'execution', 'held', 'agent', 'agnt_mrt')",
    )
    await _exec(
        admin_db,
        "INSERT INTO jobs (id, kind, status, actor_type, created_by)"
        " VALUES ('job_mrt_done', 'execution', 'completed', 'agent', 'agnt_mrt')",
    )
    await _exec(
        admin_db,
        "INSERT INTO execution_approvals (id, job_id, agent_id, credential_id, api_vendor,"
        " api_name, api_version, method, path, request_fingerprint, state, expires_at)"
        " VALUES ('exap_mrt', 'job_mrt_held', 'agnt_mrt', 'cred_mrt', 'v', 'n', '1', 'GET',"
        " '/x', 'fp_mrt', 'pending', CURRENT_TIMESTAMP)",
    )

    await asyncio.to_thread(command.downgrade, cfg, _ADMIN_PARENT)

    assert "execution_approvals" not in await _table_names(admin_db)
    async with admin_db.session() as session:
        result = await session.execute(
            text("SELECT id, status FROM jobs WHERE id LIKE 'job_mrt_%'")
        )
        rows: dict[str, str] = {str(r[0]): str(r[1]) for r in result.all()}
    assert rows == {"job_mrt_held": "cancelled", "job_mrt_done": "completed"}

    await asyncio.to_thread(command.upgrade, cfg, "head")
    assert "execution_approvals" in await _table_names(admin_db)
