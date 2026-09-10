"""Repository for permission rule set operations (theme 5 phase 1, Q-04).

Shared, reusable ordered rule lists. Flush-only, never commits.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.control.core.schema.permission_rule_sets import (
    PermissionRuleSet,
    PermissionRuleSetRule,
)


class PermissionRuleSetRepository:
    """Data access layer for PermissionRuleSet / PermissionRuleSetRule entities."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        name: str,
        description: str | None,
        created_by: str,
    ) -> PermissionRuleSet:
        rule_set = PermissionRuleSet(name=name, description=description, created_by=created_by)
        session.add(rule_set)
        await session.flush()
        return rule_set

    @staticmethod
    async def get_by_id(session: AsyncSession, rule_set_id: str) -> PermissionRuleSet | None:
        return await session.get(PermissionRuleSet, rule_set_id)

    @staticmethod
    async def get_by_name(session: AsyncSession, name: str) -> PermissionRuleSet | None:
        result = await session.execute(
            select(PermissionRuleSet).where(PermissionRuleSet.name == name)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_page(
        session: AsyncSession,
        *,
        cursor: tuple[datetime, str] | None = None,
        limit: int = 50,
    ) -> list[PermissionRuleSet]:
        """List rule sets paginated by (created_at DESC, id DESC)."""
        stmt = select(PermissionRuleSet).order_by(
            PermissionRuleSet.created_at.desc(), PermissionRuleSet.id.desc()
        )
        if cursor is not None:
            cursor_ts, cursor_id = cursor
            stmt = stmt.where(
                (PermissionRuleSet.created_at < cursor_ts)
                | ((PermissionRuleSet.created_at == cursor_ts) & (PermissionRuleSet.id < cursor_id))
            )
        result = await session.execute(stmt.limit(limit))
        return list(result.scalars().all())

    @staticmethod
    async def delete_by_id(session: AsyncSession, rule_set_id: str) -> bool:
        """Delete a rule set; its rules go with it via the FK CASCADE."""
        result = await session.execute(
            delete(PermissionRuleSet).where(PermissionRuleSet.id == rule_set_id)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    @staticmethod
    async def list_rules(session: AsyncSession, rule_set_id: str) -> list[PermissionRuleSetRule]:
        """List a set's rules ordered by sequence (user rules first, system last)."""
        result = await session.execute(
            select(PermissionRuleSetRule)
            .where(PermissionRuleSetRule.rule_set_id == rule_set_id)
            .order_by(
                PermissionRuleSetRule.is_system.asc(),
                PermissionRuleSetRule.sequence.asc(),
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def rule_counts(session: AsyncSession, rule_set_ids: list[str]) -> dict[str, int]:
        """Return ``{rule_set_id: rule_count}`` for the given ids (0s omitted)."""
        if not rule_set_ids:
            return {}
        result = await session.execute(
            select(PermissionRuleSetRule.rule_set_id, func.count())
            .where(PermissionRuleSetRule.rule_set_id.in_(rule_set_ids))
            .group_by(PermissionRuleSetRule.rule_set_id)
        )
        return {str(row[0]): int(row[1]) for row in result.fetchall()}

    @staticmethod
    async def replace_user_rules(
        session: AsyncSession,
        rule_set_id: str,
        rules: list[dict[str, object]],
        *,
        created_by: str,
    ) -> list[PermissionRuleSetRule]:
        """Delete non-system rows for the set, insert new rows with sequential ordering.

        The delete is flushed before the inserts so replacement sequences
        never collide with the rows they replace under
        ``uq_permission_rule_set_rules_seq``.
        """
        await session.execute(
            delete(PermissionRuleSetRule).where(
                PermissionRuleSetRule.rule_set_id == rule_set_id,
                PermissionRuleSetRule.is_system.is_(False),
            )
        )
        await session.flush()
        for idx, rule_data in enumerate(rules):
            session.add(
                PermissionRuleSetRule(
                    rule_set_id=rule_set_id,
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
            )
        await session.flush()
        return await PermissionRuleSetRepository.list_rules(session, rule_set_id)
