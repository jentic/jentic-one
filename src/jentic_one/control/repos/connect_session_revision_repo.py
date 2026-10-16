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
    async def get_by_id(session: AsyncSession, revision_id: str) -> ConnectSessionRevision | None:
        stmt = select(ConnectSessionRevision).where(ConnectSessionRevision.id == revision_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_for_session(
        session: AsyncSession, session_id: str
    ) -> list[ConnectSessionRevision]:
        """Every revision of the session, oldest first."""
        stmt = (
            select(ConnectSessionRevision)
            .where(ConnectSessionRevision.session_id == session_id)
            .order_by(ConnectSessionRevision.number.asc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())
