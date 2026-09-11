"""Integration tests for the satisfaction predicates on ``PrerequisiteRepository``.

These back the ``already_satisfied`` enrichment on single-request GETs (issue
#826): ``get_agent_credential_binding`` answers "is this credential:bind's
outcome already in effect?" (it also backs the per-binding permission
endpoints), and ``actor_scope_grant_exists`` mirrors the uniqueness key of the
idempotent scope-grant effect. All run against a real admin DB.
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
    """Seed an agent holding one credential binding and one scope grant."""

    async def _cleanup() -> None:
        async with admin_db.session() as session:
            await session.execute(
                text("DELETE FROM actor_scope_grants WHERE actor_id = :aid"),
                {"aid": _AGENT_ID},
            )
            await session.execute(
                text("DELETE FROM agent_credential_bindings WHERE agent_id = :aid"),
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
                "INSERT INTO agent_credential_bindings (id, agent_id, credential_id, suspended) "
                "VALUES ('acb_satisfaction_1', :aid, 'cred_satisfaction_bound', true)"
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


async def test_get_agent_credential_binding(
    admin_db: DatabaseSession, seed_admin_rows: None
) -> None:
    """The credential:bind satisfaction probe: row (with its suspended flag and
    rule_set_id) when the direct binding exists, None otherwise.

    A SUSPENDED binding still returns — suspension is a broker-derivation
    cut-off, not an un-bind, so the request's outcome is still "in effect" for
    the ``already_satisfied`` annotator and the per-binding rules endpoints.
    """
    async with admin_db.session() as session:
        row = await PrerequisiteRepository.get_agent_credential_binding(
            session, agent_id=_AGENT_ID, credential_id="cred_satisfaction_bound"
        )
        assert row is not None
        assert row.binding_id == "acb_satisfaction_1"
        assert row.suspended is True
        assert row.rule_set_id is None

        assert (
            await PrerequisiteRepository.get_agent_credential_binding(
                session, agent_id=_AGENT_ID, credential_id="cred_satisfaction_other"
            )
            is None
        )
        assert (
            await PrerequisiteRepository.get_agent_credential_binding(
                session,
                agent_id="agnt_satisfaction_absent",
                credential_id="cred_satisfaction_bound",
            )
            is None
        )
