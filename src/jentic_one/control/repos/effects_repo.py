"""Repository for the connect-session flow's cross-database operations.

Uses raw SQL for admin-DB reads/writes to avoid importing admin ORM models —
the control module must not import from the admin module. Uses ON CONFLICT DO
NOTHING for idempotent inserts without requiring rollback.
"""

from __future__ import annotations

from typing import NamedTuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.shared.db.ids import generate_ksuid


class AgentOwnerRow(NamedTuple):
    """Owner and lifecycle status of an agent, read from the admin DB."""

    owner_id: str | None
    status: str
    name: str | None = None


class EffectsRepository:
    """Cross-DB reads/writes for the connect-session flow, using raw SQL for admin-DB tables."""

    @staticmethod
    async def get_agent_owner(session: AsyncSession, agent_id: str) -> AgentOwnerRow | None:
        """Return the agent's owner, status and name via raw SQL (admin DB), ``None`` if missing.

        Cross-DB seam for the connect flow's agent checks: the control-side
        session routes verify the target agent is owned by the caller, and
        ``:confirm`` also refuses an agent that is no longer usable (archived,
        disabled, rejected) before writing a binding in its name; ``name``
        labels the ``connect_session.created`` event. The broker
        and control modules may not import admin ORM models. ``owner_id`` is
        nullable — an existing but ownerless agent has ``owner_id=None``, not
        "missing".
        """
        result = await session.execute(
            text("SELECT owner_id, status, name FROM agents WHERE id = :agent_id"),
            {"agent_id": agent_id},
        )
        row = result.first()
        if row is None:
            return None
        return AgentOwnerRow(
            owner_id=str(row[0]) if row[0] is not None else None,
            status=str(row[1]),
            name=str(row[2]) if row[2] is not None else None,
        )

    @staticmethod
    async def bind_agent_to_credential(
        session: AsyncSession,
        *,
        agent_id: str,
        credential_id: str,
        rule_set_id: str | None,
        created_by: str,
    ) -> tuple[str, bool]:
        """Create a direct agent↔credential binding idempotently via raw SQL (admin DB).

        Returns ``(binding_id, already_bound)``. An existing binding is left
        untouched (its ``rule_set_id``/``suspended`` state is authoritative) —
        the retry/reconcile path must converge without clobbering operator
        edits made between attempts.
        """
        binding_id = generate_ksuid("acb")
        result = await session.execute(
            text(
                "INSERT INTO agent_credential_bindings "
                "(id, agent_id, credential_id, rule_set_id, created_by) "
                "VALUES (:id, :agent_id, :credential_id, :rule_set_id, :created_by) "
                "ON CONFLICT (agent_id, credential_id) DO NOTHING "
                "RETURNING id"
            ),
            {
                "id": binding_id,
                "agent_id": agent_id,
                "credential_id": credential_id,
                "rule_set_id": rule_set_id,
                "created_by": created_by,
            },
        )
        inserted_id = result.scalar_one_or_none()
        if inserted_id is not None:
            await session.flush()
            return inserted_id, False

        existing = await session.execute(
            text(
                "SELECT id FROM agent_credential_bindings "
                "WHERE agent_id = :agent_id AND credential_id = :credential_id LIMIT 1"
            ),
            {"agent_id": agent_id, "credential_id": credential_id},
        )
        return existing.scalar_one(), True

    @staticmethod
    async def list_suspended_credential_ids(session: AsyncSession, agent_id: str) -> list[str]:
        """The credentials the agent holds a suspended binding to, via raw SQL (admin DB).

        A suspended binding is the operator's reversible cut-off for that
        agent and credential; the connect flow refuses a new connect that the
        suspended credential's API identity covers.
        """
        result = await session.execute(
            text(
                "SELECT credential_id FROM agent_credential_bindings "
                "WHERE agent_id = :agent_id AND suspended = true"
            ),
            {"agent_id": agent_id},
        )
        return [str(row[0]) for row in result.all()]

    @staticmethod
    async def unbind_agent_from_credential(
        session: AsyncSession,
        *,
        agent_id: str,
        credential_id: str,
    ) -> bool:
        """Delete a direct agent↔credential binding via raw SQL (admin DB).

        Mirror of ``bind_agent_to_credential`` for the connect flow's
        unhappy-terminal sweep: the pending credential is deleted
        control-side (its ``agent_permission_rules`` rows cascade with it),
        but the admin-DB binding row is cross-DB (no FK), so it must be
        removed explicitly or a ghost binding outlives the credential.
        Idempotent — returns True when a row was deleted.
        """
        result = await session.execute(
            text(
                "DELETE FROM agent_credential_bindings "
                "WHERE agent_id = :agent_id AND credential_id = :credential_id"
            ),
            {"agent_id": agent_id, "credential_id": credential_id},
        )
        await session.flush()
        return bool(getattr(result, "rowcount", 0))
