"""Cross-database sweep of a direct binding's inline rules for the auth surface.

Uses raw SQL (``text()``) against the control database so the auth module never
imports the control ORM — the auth/control module boundary (enforced by
``tests/arch/test_module_boundaries.py``) forbids a direct cross-module import.
Same convention as ``CredentialRefRepository``.

The only consumer is the purge arm of the direct unbind: purging deletes the
``(agent, credential)`` binding outright, and its inline
``agent_permission_rules`` go with it. Otherwise a later re-bind of the same
pair would start with ``rule_set_id`` cleared and pick the old inline rules
back up — rules that were dormant under an attached shared set, or that the
caller who re-binds never authored.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class BindingRuleRepository:
    """Deletes a direct binding's inline permission rules in the control DB."""

    @staticmethod
    async def delete_for_binding(
        session: AsyncSession, *, agent_id: str, credential_id: str
    ) -> int:
        """Delete every inline rule (user and system) for the pair; returns the row count."""
        stmt = text(
            "DELETE FROM agent_permission_rules "
            "WHERE agent_id = :agent_id AND credential_id = :credential_id"
        )
        result = await session.execute(stmt, {"agent_id": agent_id, "credential_id": credential_id})
        return int(result.rowcount)  # type: ignore[attr-defined]
