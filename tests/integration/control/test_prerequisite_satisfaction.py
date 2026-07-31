"""Integration tests for the satisfaction predicates on ``PrerequisiteRepository``.

These back the ``already_satisfied`` enrichment on single-request GETs (issue
#826): ``agent_bound_to_any_toolkit`` answers "is this toolkit:bind's outcome
already in effect for any candidate the reference resolved to?", and
``actor_scope_grant_exists`` mirrors the uniqueness key of the idempotent
scope-grant effect. Both run against a real admin DB.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import text

from jentic_one.control.repos.prerequisite_repo import PrerequisiteRepository
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration

_AGENT_ID = "agnt_satisfaction_test"
_OWNER_ID = "usr_satisfaction_owner"


@pytest.fixture()
async def seed_admin_rows(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Seed an agent bound to one toolkit and holding one scope grant."""

    async def _cleanup() -> None:
        async with admin_db.session() as session:
            await session.execute(
                text("DELETE FROM actor_scope_grants WHERE actor_id = :aid"),
                {"aid": _AGENT_ID},
            )
            await session.execute(
                text("DELETE FROM agent_toolkit_bindings WHERE agent_id = :aid"),
                {"aid": _AGENT_ID},
            )
            await session.execute(text("DELETE FROM agents WHERE id = :aid"), {"aid": _AGENT_ID})
            await session.commit()

    await _cleanup()
    async with admin_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO agents (id, name, registered_by, status) "
                "VALUES (:id, 'satisfaction-test-agent', :owner, 'active')"
            ),
            {"id": _AGENT_ID, "owner": _OWNER_ID},
        )
        await session.execute(
            text(
                "INSERT INTO agent_toolkit_bindings (id, agent_id, toolkit_id) "
                "VALUES ('atb_satisfaction_1', :aid, 'tk_satisfaction_bound')"
            ),
            {"aid": _AGENT_ID},
        )
        await session.execute(
            text(
                "INSERT INTO actor_scope_grants (id, actor_id, actor_type, scope, granted_by) "
                "VALUES ('asg_satisfaction_1', :aid, 'agent', 'apis:write', :owner)"
            ),
            {"aid": _AGENT_ID, "owner": _OWNER_ID},
        )
        await session.commit()
    yield
    await _cleanup()


async def test_agent_bound_to_any_toolkit(admin_db: DatabaseSession, seed_admin_rows: None) -> None:
    """Bound to any candidate → True; disjoint candidates → False; empty → False."""
    async with admin_db.session() as session:
        assert await PrerequisiteRepository.agent_bound_to_any_toolkit(
            session, agent_id=_AGENT_ID, toolkit_ids=["tk_satisfaction_bound"]
        )
        # A multi-candidate probe (an ambiguous reference) matches on any hit.
        assert await PrerequisiteRepository.agent_bound_to_any_toolkit(
            session,
            agent_id=_AGENT_ID,
            toolkit_ids=["tk_satisfaction_other", "tk_satisfaction_bound"],
        )
        assert not await PrerequisiteRepository.agent_bound_to_any_toolkit(
            session, agent_id=_AGENT_ID, toolkit_ids=["tk_satisfaction_other"]
        )
        # Empty candidate list short-circuits without querying.
        assert not await PrerequisiteRepository.agent_bound_to_any_toolkit(
            session, agent_id=_AGENT_ID, toolkit_ids=[]
        )
        assert not await PrerequisiteRepository.agent_bound_to_any_toolkit(
            session, agent_id="agnt_satisfaction_absent", toolkit_ids=["tk_satisfaction_bound"]
        )


async def test_actor_scope_grant_exists(admin_db: DatabaseSession, seed_admin_rows: None) -> None:
    """Exists exactly when the (actor_id, scope) grant row does."""
    async with admin_db.session() as session:
        assert await PrerequisiteRepository.actor_scope_grant_exists(
            session, actor_id=_AGENT_ID, scope="apis:write"
        )
        assert not await PrerequisiteRepository.actor_scope_grant_exists(
            session, actor_id=_AGENT_ID, scope="capabilities:execute"
        )
        assert not await PrerequisiteRepository.actor_scope_grant_exists(
            session, actor_id="agnt_satisfaction_absent", scope="apis:write"
        )
