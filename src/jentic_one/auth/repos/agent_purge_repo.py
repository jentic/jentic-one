"""Control-database sweep of an agent's rows for the auth-side agent purge.

Uses raw SQL (``text()``) against the control database so the auth module never
imports the control ORM — the module boundaries (enforced by
``tests/arch/test_module_boundaries.py``) forbid a direct cross-module import.
Same convention as ``BindingRuleRepository`` and ``CredentialRefRepository``.
The table and column names used here are pinned against the control ORM by
``tests/unit/auth/test_agent_purge_repo.py``.

The only consumer is ``AgentService.purge``: every row here keys the agent by a
plain id with no FK into the admin ``agents`` table, so the hard delete sweeps
them explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: Connect sessions that target the agent or that the agent opened.
_AGENT_SESSIONS = "(agent_id = :agent_id OR initiator_actor_id = :agent_id)"


@dataclass(frozen=True)
class ControlPurgeCounts:
    """What one agent purge did in the control database."""

    pending_credentials: int
    connect_sessions: int
    connect_session_outcomes: int
    permission_rules: int
    #: Credentials the agent created that are not pending; the purge keeps them.
    retained_credential_ids: list[str]


class AgentControlPurgeRepository:
    """Deletes one agent's rows from the control database."""

    @staticmethod
    async def delete_for_agent(session: AsyncSession, agent_id: str) -> ControlPurgeCounts:
        """Tear down the agent's connect sessions and delete its permission rules.

        A session's ``pending`` credential exists only for that session, so it is
        deleted with it, the same as a session that expires. The delete cascades
        to the session row and the credential's flow-specific rows. Sessions over
        a credential that is no longer pending go as rows only, and the credential
        stays. Credentials the agent created that are not pending are kept and
        their ids returned, so the caller can record them.
        """
        params = {"agent_id": agent_id}
        pending = await session.execute(
            text(
                "DELETE FROM credentials WHERE state = 'pending' AND id IN "
                f"(SELECT credential_id FROM connect_sessions WHERE {_AGENT_SESSIONS})"
            ),
            params,
        )
        sessions = await session.execute(
            text(f"DELETE FROM connect_sessions WHERE {_AGENT_SESSIONS}"), params
        )
        outcomes = await session.execute(
            text(
                "DELETE FROM connect_session_outcomes "
                "WHERE agent_id = :agent_id OR created_by = :agent_id"
            ),
            params,
        )
        rules = await session.execute(
            text("DELETE FROM agent_permission_rules WHERE agent_id = :agent_id"), params
        )
        retained = await session.execute(
            text("SELECT id FROM credentials WHERE created_by = :agent_id ORDER BY id"), params
        )
        return ControlPurgeCounts(
            pending_credentials=int(pending.rowcount),  # type: ignore[attr-defined]
            connect_sessions=int(sessions.rowcount),  # type: ignore[attr-defined]
            connect_session_outcomes=int(outcomes.rowcount),  # type: ignore[attr-defined]
            permission_rules=int(rules.rowcount),  # type: ignore[attr-defined]
            retained_credential_ids=[str(row[0]) for row in retained.all()],
        )
