"""Repository for ConnectSessionRevision rows (append-only versions of a session's ask)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.control.core.schema.connect_session_revisions import ConnectSessionRevision
from jentic_one.control.core.schema.connect_sessions import ConnectSession


class ConnectSessionRevisionRepository:
    """Data access layer for connect-session revisions — flush-only, never commits."""

    @staticmethod
    async def append(
        session: AsyncSession,
        *,
        session_id: str,
        proposal: dict[str, Any],
        result: dict[str, Any],
        digest: str,
        created_by: str | None,
    ) -> ConnectSessionRevision:
        """Write the session's next revision and make it the current one.

        Numbering comes from the session's highest revision; the unique
        ``(session_id, number)`` index refuses a concurrent duplicate, so the
        caller serialises amends on the session row's state.
        """
        highest = await session.execute(
            select(func.max(ConnectSessionRevision.number)).where(
                ConnectSessionRevision.session_id == session_id
            )
        )
        number = int(highest.scalar_one() or 0) + 1
        row = ConnectSessionRevision(
            session_id=session_id,
            number=number,
            proposal=proposal,
            result=result,
            digest=digest,
            created_by=created_by,
        )
        session.add(row)
        await session.flush()
        await session.execute(
            update(ConnectSession)
            .where(ConnectSession.id == session_id)
            .values(current_revision_id=row.id)
        )
        await session.flush()
        return row

    @staticmethod
    async def insert(session: AsyncSession, revision: ConnectSessionRevision) -> None:
        """Write a revision the caller has already numbered and pointed the session at."""
        session.add(revision)
        await session.flush()

    @staticmethod
    async def get_by_id(
        session: AsyncSession, revision_id: str, *, session_id: str | None = None
    ) -> ConnectSessionRevision | None:
        """One revision, scoped to its session when the caller knows it.

        A caller-supplied id resolved without the session would let a revision
        of one session answer for another; every caller here has the session in
        hand, so it names it.
        """
        stmt = select(ConnectSessionRevision).where(ConnectSessionRevision.id == revision_id)
        if session_id is not None:
            stmt = stmt.where(ConnectSessionRevision.session_id == session_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_for_session(
        session: AsyncSession,
        session_id: str,
        *,
        limit: int | None = None,
        after_revision_id: str | None = None,
    ) -> list[ConnectSessionRevision]:
        """The session's revisions, oldest first.

        Ordered and paged on ``number``, which is monotonic within the session
        and carries the unique ``(session_id, number)`` index. ``created_at``
        would not do: it is stamped by the app clock on one path and the
        database clock on another, so two revisions of one session can compare
        out of order and a page boundary taken there would skip one.

        ``limit`` bounds the page; the caller asks for one more than the page
        size to learn whether more remain. ``after_revision_id`` continues
        after a revision the caller already has, resolved to its number in the
        same statement.
        """
        stmt = select(ConnectSessionRevision).where(ConnectSessionRevision.session_id == session_id)
        if after_revision_id is not None:
            after = (
                select(ConnectSessionRevision.number)
                .where(ConnectSessionRevision.id == after_revision_id)
                .where(ConnectSessionRevision.session_id == session_id)
                .scalar_subquery()
            )
            stmt = stmt.where(ConnectSessionRevision.number > after)
        stmt = stmt.order_by(ConnectSessionRevision.number.asc())
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await session.execute(stmt)
        return list(result.scalars().all())
