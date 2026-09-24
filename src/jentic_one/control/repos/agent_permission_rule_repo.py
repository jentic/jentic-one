"""Repository for agent-credential-binding permission rule operations (theme 5 phase 1).

The direct-binding analogue of ``ToolkitPermissionRepository``, with one
deliberate semantic difference: there is **no vendor pooling**. A binding's
rules are a single ordered list keyed ``(agent_id, credential_id)`` —
first-match-wins over exactly the rules attached to that binding. (Toolkit
rules pool across same-vendor bindings because the broker's toolkit query
joins through the binding table; the per-binding model exists to remove
that ambiguity.)
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule


class AgentPermissionRuleRepository:
    """Data access layer for AgentPermissionRule entities — flush-only, never commits."""

    @staticmethod
    async def list_rules(
        session: AsyncSession,
        agent_id: str,
        credential_id: str,
        *,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> list[AgentPermissionRule]:
        """List rules ordered by sequence (user rules first, system rules last)."""
        stmt = (
            select(AgentPermissionRule)
            .where(
                AgentPermissionRule.agent_id == agent_id,
                AgentPermissionRule.credential_id == credential_id,
            )
            .order_by(
                AgentPermissionRule.is_system.asc(),
                AgentPermissionRule.sequence.asc(),
            )
        )
        if filters is not None:
            for f in filters:
                stmt = stmt.where(f)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def replace_user_rules(
        session: AsyncSession,
        agent_id: str,
        credential_id: str,
        rules: list[dict[str, object]],
        *,
        created_by: str,
    ) -> list[AgentPermissionRule]:
        """Delete non-system rows for the binding, insert new rows with sequential ordering.

        The delete is flushed before the inserts so the replacement sequences
        never collide with the rows they replace under
        ``uq_agent_permission_rules_binding_seq``.
        """
        await session.execute(
            delete(AgentPermissionRule).where(
                AgentPermissionRule.agent_id == agent_id,
                AgentPermissionRule.credential_id == credential_id,
                AgentPermissionRule.is_system.is_(False),
            )
        )
        await session.flush()
        new_rules: list[AgentPermissionRule] = []
        for idx, rule_data in enumerate(rules):
            rule = AgentPermissionRule(
                agent_id=agent_id,
                credential_id=credential_id,
                effect=str(rule_data.get("effect", "allow")),
                methods=rule_data.get("methods"),
                path=rule_data.get("path"),
                match_mode=str(rule_data.get("match_mode", "regex")),
                operations=rule_data.get("operations"),
                is_system=False,
                comment=rule_data.get("comment"),
                sequence=idx,
                created_by=created_by,
            )
            session.add(rule)
            new_rules.append(rule)
        await session.flush()
        return await AgentPermissionRuleRepository.list_rules(session, agent_id, credential_id)

    @staticmethod
    async def patch_rules(
        session: AsyncSession,
        agent_id: str,
        credential_id: str,
        *,
        add: list[dict[str, object]] | None = None,
        remove: list[int] | None = None,
        created_by: str,
    ) -> list[AgentPermissionRule]:
        """Add/remove user rules per PermissionsPatchRequest semantics."""
        existing = await AgentPermissionRuleRepository.list_rules(session, agent_id, credential_id)
        user_rules = [r for r in existing if not r.is_system]

        if remove:
            remove_set = set(remove)
            to_delete = [r for i, r in enumerate(user_rules) if i in remove_set]
            for rule in to_delete:
                await session.delete(rule)
            await session.flush()
            user_rules = [r for i, r in enumerate(user_rules) if i not in remove_set]

        max_seq = max((r.sequence for r in user_rules), default=-1)
        if add:
            for idx, rule_data in enumerate(add):
                rule = AgentPermissionRule(
                    agent_id=agent_id,
                    credential_id=credential_id,
                    effect=str(rule_data.get("effect", "allow")),
                    methods=rule_data.get("methods"),
                    path=rule_data.get("path"),
                    match_mode=str(rule_data.get("match_mode", "regex")),
                    operations=rule_data.get("operations"),
                    is_system=False,
                    comment=rule_data.get("comment"),
                    sequence=max_seq + 1 + idx,
                    created_by=created_by,
                )
                session.add(rule)

        await session.flush()
        return await AgentPermissionRuleRepository.list_rules(session, agent_id, credential_id)

    @staticmethod
    async def delete_for_agent(session: AsyncSession, agent_id: str) -> int:
        """Delete all rules for an agent (application-level sweep; no cross-DB CASCADE)."""
        result = await session.execute(
            delete(AgentPermissionRule).where(AgentPermissionRule.agent_id == agent_id)
        )
        return int(result.rowcount)  # type: ignore[attr-defined]
