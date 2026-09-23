"""Repository for the unified actor directory (read-only UNION ALL view)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, String, case, literal, select, tuple_, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import CompoundSelect

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.users import User
from jentic_one.shared.models import ActorStatus, ActorType


def _build_union() -> CompoundSelect[Any]:
    """Build a UNION ALL query across users and agents.

    Service accounts left the directory in theme-8 Phase 2: every SA was
    migrated to a successor agent (which the agents arm lists).
    """
    users_q = select(
        User.id.label("id"),
        literal(ActorType.USER, type_=String).label("actor_type"),
        (User.first_name + literal(" ") + User.last_name).label("name"),
        User.active.cast(Boolean).label("active"),
        User.created_at.label("created_at"),
    )

    agents_q = select(
        Agent.id.label("id"),
        literal(ActorType.AGENT, type_=String).label("actor_type"),
        Agent.name.label("name"),
        case((Agent.status == ActorStatus.ACTIVE, literal(True)), else_=literal(False))
        .cast(Boolean)
        .label("active"),
        Agent.created_at.label("created_at"),
    )

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
    ) -> list[Any]:
        subq = _build_union().subquery()
        stmt = select(subq).order_by(subq.c.created_at.desc(), subq.c.id.desc()).limit(limit)

        if cursor_ts is not None and cursor_id is not None:
            stmt = stmt.where(
                tuple_(subq.c.created_at, subq.c.id)
                < tuple_(literal(cursor_ts), literal(cursor_id))
            )
        elif cursor_ts is not None:
            stmt = stmt.where(subq.c.created_at < cursor_ts)

        result = await session.execute(stmt)
        return list(result.all())
