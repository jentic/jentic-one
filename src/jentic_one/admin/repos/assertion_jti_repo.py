"""Repository for assertion ``jti`` single-use records — flush-only, never commits."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.assertion_jtis import AssertionJti
from jentic_one.shared.db.ids import generate_ksuid


class AssertionJtiRepository:
    """Data access layer for :class:`AssertionJti` rows."""

    @staticmethod
    async def claim(
        session: AsyncSession,
        *,
        issuer: str,
        jti_digest: str,
        expires_at: datetime,
        now: datetime,
    ) -> bool:
        """Record ``(issuer, jti_digest)`` as used; ``False`` when it already is.

        One atomic statement: an insert that, on the unique
        ``(issuer, jti_digest)`` key, takes over the existing row only when that
        row has expired (``expires_at <= now``). A live row is left untouched
        and nothing is written, so of two concurrent claims of the same key
        exactly one returns ``True``; the other waits on the first's
        transaction (PostgreSQL) or the database write lock (SQLite) and then
        sees the committed row.
        """
        insert = sqlite_insert if session.get_bind().dialect.name == "sqlite" else pg_insert
        stmt = insert(AssertionJti).values(
            id=generate_ksuid("ajti"),
            issuer=issuer,
            jti_digest=jti_digest,
            expires_at=expires_at,
            created_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[AssertionJti.issuer, AssertionJti.jti_digest],
            set_={"expires_at": stmt.excluded.expires_at, "created_at": stmt.excluded.created_at},
            where=AssertionJti.expires_at <= now,
        )
        result: CursorResult[tuple[()]] = await session.execute(stmt)  # type: ignore[assignment]
        await session.flush()
        return bool(result.rowcount)

    @staticmethod
    async def delete_expired(session: AsyncSession, *, now: datetime, limit: int) -> int:
        """Delete up to ``limit`` rows that expired at or before ``now``.

        Bounded so it can run on the token-exchange path. On PostgreSQL rows
        another transaction holds (pruning or taking over) are skipped
        (``SKIP LOCKED``), so a prune never waits on another exchange.
        """
        expired = (
            select(AssertionJti.id)
            .where(AssertionJti.expires_at <= now)
            .order_by(AssertionJti.expires_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        stmt = delete(AssertionJti).where(AssertionJti.id.in_(expired))
        result: CursorResult[tuple[()]] = await session.execute(stmt)  # type: ignore[assignment]
        await session.flush()
        return int(result.rowcount or 0)
