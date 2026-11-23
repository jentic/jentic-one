"""Repository for AuditEntry append-only log."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.shared.db.keyset import apply_desc_keyset
from jentic_one.shared.models.audit import AuditAction, AuditTargetType
from jentic_one.shared.pagination import Cursor


class AuditRepository:
    """Data access layer for AuditEntry — flush-only, never commits."""

    @staticmethod
    def _compute_diff(
        before: dict | None,  # type: ignore[type-arg]
        after: dict | None,  # type: ignore[type-arg]
    ) -> dict | None:  # type: ignore[type-arg]
        """Compute a structured diff between before and after snapshots."""
        if before is None and after is None:
            return None
        if before is None:
            return {"added": dict((after or {}).items())}
        if after is None:
            return {"removed": dict(before.items())}

        added = {k: v for k, v in after.items() if k not in before}
        removed = {k: v for k, v in before.items() if k not in after}
        modified = {
            k: {"old": before[k], "new": after[k]}
            for k in before
            if k in after and before[k] != after[k]
        }

        result: dict[str, object] = {}
        if added:
            result["added"] = added
        if removed:
            result["removed"] = removed
        if modified:
            result["modified"] = modified
        return result or None

    @staticmethod
    async def record(
        session: AsyncSession,
        *,
        action: AuditAction,
        target_type: AuditTargetType,
        target_id: str,
        actor_type: str,
        actor_id: str | None = None,
        actor_session_id: str | None = None,
        before: dict | None = None,  # type: ignore[type-arg]
        after: dict | None = None,  # type: ignore[type-arg]
        target_parent_id: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        job_id: str | None = None,
        reason: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        origin: str | None = None,
    ) -> AuditEntry:
        """Record an audit entry. Flush-only — caller manages the transaction."""
        diff = AuditRepository._compute_diff(before, after)
        entry = AuditEntry(
            action=action,
            target_type=target_type,
            target_id=target_id,
            actor_type=actor_type,
            actor_id=actor_id,
            actor_session_id=actor_session_id,
            before=before,
            after=after,
            diff=diff,
            target_parent_id=target_parent_id,
            request_id=request_id,
            trace_id=trace_id,
            job_id=job_id,
            reason=reason,
            ip_address=ip_address,
            user_agent=user_agent,
            origin=origin,
        )
        session.add(entry)
        await session.flush()
        return entry

    @staticmethod
    async def get_by_id(
        session: AsyncSession,
        audit_id: str,
        *,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> AuditEntry | None:
        if filters is None:
            return await session.get(AuditEntry, audit_id)
        stmt = select(AuditEntry).where(AuditEntry.id == audit_id)
        for f in filters:
            stmt = stmt.where(f)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_by_target(
        session: AsyncSession,
        target_type: AuditTargetType,
        target_id: str,
        *,
        limit: int = 50,
        cursor: Cursor | None = None,
        actor_id: str | None = None,
        origin: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> list[AuditEntry]:
        """List entries for a target, ordered by (occurred_at, id) DESC, keyset-paginated."""
        stmt = (
            select(AuditEntry)
            .where(AuditEntry.target_type == target_type, AuditEntry.target_id == target_id)
            .order_by(AuditEntry.occurred_at.desc(), AuditEntry.id.desc())
            .limit(limit)
        )
        stmt = apply_desc_keyset(stmt, AuditEntry.occurred_at, AuditEntry.id, cursor)
        if actor_id is not None:
            stmt = stmt.where(AuditEntry.actor_id == actor_id)
        if origin is not None:
            stmt = stmt.where(AuditEntry.origin == origin)
        if since is not None:
            stmt = stmt.where(AuditEntry.occurred_at >= since)
        if until is not None:
            stmt = stmt.where(AuditEntry.occurred_at <= until)
        for f in filters or ():
            stmt = stmt.where(f)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def list_by_actor(
        session: AsyncSession,
        actor_id: str,
        *,
        limit: int = 50,
        cursor: Cursor | None = None,
        origin: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> list[AuditEntry]:
        """List entries for an actor, ordered by (occurred_at, id) DESC, keyset-paginated."""
        stmt = (
            select(AuditEntry)
            .where(AuditEntry.actor_id == actor_id)
            .order_by(AuditEntry.occurred_at.desc(), AuditEntry.id.desc())
            .limit(limit)
        )
        stmt = apply_desc_keyset(stmt, AuditEntry.occurred_at, AuditEntry.id, cursor)
        if origin is not None:
            stmt = stmt.where(AuditEntry.origin == origin)
        if since is not None:
            stmt = stmt.where(AuditEntry.occurred_at >= since)
        if until is not None:
            stmt = stmt.where(AuditEntry.occurred_at <= until)
        for f in filters or ():
            stmt = stmt.where(f)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def list_all(
        session: AsyncSession,
        *,
        limit: int = 50,
        cursor: Cursor | None = None,
        origin: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> list[AuditEntry]:
        """Global feed ordered by (occurred_at, id) DESC with optional time bounds."""
        stmt = (
            select(AuditEntry)
            .order_by(AuditEntry.occurred_at.desc(), AuditEntry.id.desc())
            .limit(limit)
        )
        stmt = apply_desc_keyset(stmt, AuditEntry.occurred_at, AuditEntry.id, cursor)
        if origin is not None:
            stmt = stmt.where(AuditEntry.origin == origin)
        if since is not None:
            stmt = stmt.where(AuditEntry.occurred_at >= since)
        if until is not None:
            stmt = stmt.where(AuditEntry.occurred_at <= until)
        for f in filters or ():
            stmt = stmt.where(f)
        result = await session.execute(stmt)
        return list(result.scalars().all())
