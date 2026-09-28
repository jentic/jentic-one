"""Integration tests for ``UpgradeStepService`` — the post-migration data steps.

Runs against real control + admin databases on both dialects (Postgres takes
the advisory-lock path; SQLite the serialised-writer path). The runner-level
behaviour (full upgrade only, exactly once, purge survives a re-run) is pinned
by ``tests/unit/test_migration_upgrade_steps.py``; this suite pins the
service's own decisions: the ledger, the skip on an operator-acknowledged
flatten, and concurrent runs converging.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, text

from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.toolkit_credential_bindings import ToolkitCredentialBinding
from jentic_one.control.core.schema.toolkit_flattening_acks import ToolkitFlatteningAck
from jentic_one.control.core.schema.toolkit_permission_rules import ToolkitPermissionRule
from jentic_one.control.core.schema.toolkits import Toolkit
from jentic_one.control.core.schema.upgrade_steps import UpgradeStep
from jentic_one.control.services.upgrade_steps import (
    STEP_FLATTEN_TOOLKITS,
    STEP_RETIRE_TOOLKIT_KEYS,
    UpgradeStepService,
)
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration

_OWNER = "usr_ustest_owner"
_AGENT = "agnt_ustest_a"
_TOOLKIT = "tk_ustest_a"
_CRED = "cred_ustest_a"


@pytest.fixture()
async def clean_tables(
    control_db: DatabaseSession, admin_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    """Wipe the ledger, the job-scanned legacy tables, and this module's rows."""

    async def _cleanup() -> None:
        async with control_db.session() as session:
            await session.execute(delete(UpgradeStep))
            await session.execute(delete(ToolkitFlatteningAck))
            await session.execute(delete(ToolkitPermissionRule))
            await session.execute(delete(ToolkitCredentialBinding))
            await session.execute(delete(Toolkit))
            await session.execute(
                text("DELETE FROM permission_rule_sets WHERE name LIKE 'theme5-flattening:%'")
            )
            await session.execute(text("DELETE FROM credentials WHERE id LIKE 'cred_ustest%'"))
            await session.commit()
        async with admin_db.session() as session:
            await session.execute(text("DELETE FROM agent_toolkit_bindings"))
            await session.execute(
                text("DELETE FROM agent_credential_bindings WHERE agent_id LIKE 'agnt_ustest%'")
            )
            await session.execute(
                text("DELETE FROM audit_entries WHERE actor_id = 'system:theme5-flattening'")
            )
            await session.execute(text("DELETE FROM agents WHERE id LIKE 'agnt_ustest%'"))
            await session.execute(text("DELETE FROM users WHERE id = :owner"), {"owner": _OWNER})
            await session.commit()

    await _cleanup()
    yield
    await _cleanup()


@pytest.fixture()
async def toolkit_bound_agent(
    control_db: DatabaseSession, admin_db: DatabaseSession, clean_tables: None
) -> None:
    """One agent reaching one credential through one toolkit with one allow rule."""
    async with control_db.session() as session:
        session.add(Toolkit(id=_TOOLKIT, name="us-toolkit", active=True, created_by=_OWNER))
        session.add(
            Credential(
                id=_CRED,
                type="token_value",
                name="us-cred",
                api_vendor="ustest.local",
                created_by=_OWNER,
            )
        )
        await session.flush()
        session.add(
            ToolkitCredentialBinding(toolkit_id=_TOOLKIT, credential_id=_CRED, created_by=_OWNER)
        )
        session.add(
            ToolkitPermissionRule(
                toolkit_id=_TOOLKIT,
                credential_id=_CRED,
                effect="allow",
                path="/.*",
                match_mode="regex",
                sequence=0,
                created_by=_OWNER,
            )
        )
        await session.commit()
    async with admin_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, email, first_name, last_name)"
                " VALUES (:id, 'ustest-owner@test.local', 'U', 'S')"
            ),
            {"id": _OWNER},
        )
        await session.execute(
            text(
                "INSERT INTO agents (id, name, registered_by, status)"
                " VALUES (:id, 'us-agent', :owner, 'approved')"
            ),
            {"id": _AGENT, "owner": _OWNER},
        )
        await session.execute(
            text(
                "INSERT INTO agent_toolkit_bindings (id, agent_id, toolkit_id, created_by)"
                " VALUES ('atb_ustest_1', :agent, :toolkit, :owner)"
            ),
            {"agent": _AGENT, "toolkit": _TOOLKIT, "owner": _OWNER},
        )
        await session.commit()


async def _direct_binding_count(admin_db: DatabaseSession) -> int:
    async with admin_db.session() as session:
        row = (
            await session.execute(
                text("SELECT count(*) AS n FROM agent_credential_bindings WHERE agent_id = :a"),
                {"a": _AGENT},
            )
        ).one()
    return int(row.n)


async def _ledger(control_db: DatabaseSession) -> list[str]:
    async with control_db.session() as session:
        rows = (await session.execute(text("SELECT name FROM upgrade_steps"))).all()
    return [str(r.name) for r in rows]


async def test_run_flattens_and_ledgers_the_step(
    integration_context: Context,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    toolkit_bound_agent: None,
) -> None:
    outcomes = {o.name: o for o in await UpgradeStepService(integration_context).run()}

    assert outcomes[STEP_RETIRE_TOOLKIT_KEYS].failed is False
    flatten = outcomes[STEP_FLATTEN_TOOLKITS]
    assert flatten.action == "performed"
    assert flatten.summary["created"] == 1
    assert flatten.summary["created_default_deny"] == 0
    assert await _direct_binding_count(admin_db) == 1
    # Key retirement is re-run every time (idempotent per key), never ledgered.
    assert await _ledger(control_db) == [STEP_FLATTEN_TOOLKITS]


async def test_acknowledged_flatten_is_not_redone(
    integration_context: Context,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    toolkit_bound_agent: None,
) -> None:
    """An operator who already flattened and verified keeps their curated state."""
    async with control_db.transaction() as session:
        session.add(
            ToolkitFlatteningAck(
                acknowledged_at=dt.datetime.now(dt.UTC),
                legacy_pair_count=1,
                direct_binding_count=1,
                report_finding_count=0,
                tool_version="test",
                created_by=_OWNER,
            )
        )

    outcomes = {o.name: o for o in await UpgradeStepService(integration_context).run()}

    assert outcomes[STEP_FLATTEN_TOOLKITS].action == "skipped"
    assert outcomes[STEP_FLATTEN_TOOLKITS].summary["reason"] == "verified_flatten_acknowledged"
    assert await _direct_binding_count(admin_db) == 0
    assert STEP_FLATTEN_TOOLKITS in await _ledger(control_db)


async def test_concurrent_runs_flatten_once(
    integration_context: Context,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    toolkit_bound_agent: None,
) -> None:
    """Two runners (e.g. a retried hook overlapping its predecessor) converge."""
    runs = await asyncio.gather(
        UpgradeStepService(integration_context).run(),
        UpgradeStepService(integration_context).run(),
    )

    actions = sorted(next(o for o in run if o.name == STEP_FLATTEN_TOOLKITS).action for run in runs)
    if control_db.engine.dialect.name == "postgresql":
        # The run lock serialises the runners: the second sees the ledger row.
        assert actions == ["already_done", "performed"]
    else:
        # No advisory locks on SQLite; the flatten's natural-key idempotency
        # and the ledger's unique name make an overlapping run harmless.
        assert set(actions) <= {"already_done", "performed"}
    assert await _direct_binding_count(admin_db) == 1
    assert await _ledger(control_db) == [STEP_FLATTEN_TOOLKITS]
