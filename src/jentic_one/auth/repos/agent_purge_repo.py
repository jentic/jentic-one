"""Cross-database sweeps of an agent's rows for the auth-side agent purge.

Uses raw SQL (``text()``) against the control and registry databases so the
auth module never imports their ORMs — the module boundaries (enforced by
``tests/arch/test_module_boundaries.py``) forbid a direct cross-module import.
Same convention as ``BindingRuleRepository`` and ``CredentialRefRepository``.

The only consumer is ``AgentService.purge``: every row here keys the agent by a
plain id with no FK into the admin ``agents`` table, so the hard delete sweeps
them explicitly. Credentials are deliberately out of scope — they belong to
their owner, not to the agent.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

#: Connect-session states with no flow left to drive. Live sessions
#: (``created`` / ``awaiting_app`` / ``polling``) are left in place so the
#: stale-session sweep still settles them and their pending credential.
_LIVE_CONNECT_SESSION_STATES = ("created", "awaiting_app", "polling")


@dataclass(frozen=True)
class ControlPurgeCounts:
    """Rows removed from the control database by one agent purge."""

    permission_rules: int
    connect_sessions: int
    connect_session_outcomes: int


class AgentControlPurgeRepository:
    """Deletes one agent's rows from the control database."""

    @staticmethod
    async def delete_for_agent(session: AsyncSession, agent_id: str) -> ControlPurgeCounts:
        """Delete the agent's permission rules, finished connect sessions and outcomes."""
        rules = await session.execute(
            text("DELETE FROM agent_permission_rules WHERE agent_id = :agent_id"),
            {"agent_id": agent_id},
        )
        sessions_stmt = text(
            "DELETE FROM connect_sessions "
            "WHERE (agent_id = :agent_id OR initiator_actor_id = :agent_id) "
            "AND state NOT IN :live_states"
        ).bindparams(bindparam("live_states", expanding=True))
        sessions = await session.execute(
            sessions_stmt,
            {"agent_id": agent_id, "live_states": list(_LIVE_CONNECT_SESSION_STATES)},
        )
        outcomes = await session.execute(
            text(
                "DELETE FROM connect_session_outcomes "
                "WHERE agent_id = :agent_id OR created_by = :agent_id"
            ),
            {"agent_id": agent_id},
        )
        return ControlPurgeCounts(
            permission_rules=int(rules.rowcount),  # type: ignore[attr-defined]
            connect_sessions=int(sessions.rowcount),  # type: ignore[attr-defined]
            connect_session_outcomes=int(outcomes.rowcount),  # type: ignore[attr-defined]
        )


class AgentRegistryPurgeRepository:
    """Deletes one agent's rows from the registry database."""

    @staticmethod
    async def delete_for_agent(session: AsyncSession, agent_id: str) -> int:
        """Delete every note the agent authored; returns the row count."""
        result = await session.execute(
            text("DELETE FROM notes WHERE created_by = :agent_id"), {"agent_id": agent_id}
        )
        return int(result.rowcount)  # type: ignore[attr-defined]
