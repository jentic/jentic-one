"""Repository for the unified actor directory (read-only UNION ALL view)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, String, case, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.expression import CompoundSelect

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.users import User
from jentic_one.shared.db.keyset import desc_keyset_predicate
from jentic_one.shared.models import ActorStatus, ActorType


def _build_union(
    agent_filters: Sequence[ColumnElement[bool]] = (),
    user_filters: Sequence[ColumnElement[bool]] = (),
) -> CompoundSelect[Any]:
    """Build a UNION ALL query across users and agents.

    ``agent_filters`` narrows the agents arm and ``user_filters`` the users arm
    (each the caller's visibility for that model); empty means unrestricted.

    Service accounts left the directory in theme-8 Phase 2: every SA was
    migrated to a successor agent (which the agents arm lists).
    """
    users_q = select(
        User.id.label("id"),
        literal(ActorType.USER, type_=String).label("actor_type"),
        (User.first_name + literal(" ") + User.last_name).label("name"),
        User.active.cast(Boolean).label("active"),
        User.created_at.label("created_at"),
    ).where(*user_filters)

    agents_q = select(
        Agent.id.label("id"),
        literal(ActorType.AGENT, type_=String).label("actor_type"),
        Agent.name.label("name"),
        case((Agent.status == ActorStatus.ACTIVE, literal(True)), else_=literal(False))
        .cast(Boolean)
        .label("active"),
        Agent.created_at.label("created_at"),
    ).where(*agent_filters)

    return union_all(users_q, agents_q)


class ActorDirectoryRepository:
    """Read-only actor directory — UNION ALL across users and agents."""

    @staticmethod
    async def list_all(
        session: AsyncSession,
        *,
        limit: int = 1000,
        cursor_ts: datetime | None = None,
        cursor_id: str | None = None,
        agent_filters: Sequence[ColumnElement[bool]] = (),
        user_filters: Sequence[ColumnElement[bool]] = (),
    ) -> list[Any]:
        subq = _build_union(agent_filters, user_filters).subquery()
        stmt = select(subq).order_by(subq.c.created_at.desc(), subq.c.id.desc()).limit(limit)

        if cursor_ts is not None and cursor_id is not None:
            stmt = stmt.where(
                desc_keyset_predicate(subq.c.created_at, subq.c.id, cursor_ts, cursor_id)
            )

        result = await session.execute(stmt)
        return list(result.all())

    @staticmethod
    async def get_by_ids(
        session: AsyncSession,
        ids: Sequence[str],
        *,
        agent_filters: Sequence[ColumnElement[bool]] = (),
        user_filters: Sequence[ColumnElement[bool]] = (),
    ) -> list[Any]:
        """Return the directory rows whose id is in ``ids``; unknown ids are skipped.

        ``agent_filters`` and ``user_filters`` restrict which agent and user
        rows can match. The id bound is applied inside each arm, so every
        filter is evaluated only for the requested ids.
        """
        if not ids:
            return []
        id_list = list(ids)
        subq = _build_union(
            [Agent.id.in_(id_list), *agent_filters],
            [User.id.in_(id_list), *user_filters],
        ).subquery()
        stmt = select(subq).order_by(subq.c.id)
        result = await session.execute(stmt)
        return list(result.all())
