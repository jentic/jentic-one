"""Repository for connect-state nonce consumption (single-use enforcement)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import CursorResult, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.control.core.schema.connect_nonces import ConnectNonce


class ConnectNonceRepository:
    """Data access layer for connect nonce consumption — flush-only, never commits."""

    @staticmethod
    async def consume(
        session: AsyncSession,
        *,
        nonce: str,
        credential_id: str,
        expires_at: datetime,
        created_by: str,
    ) -> bool:
        """Consume a nonce. Returns True if consumed, False if already used/expired."""
        if expires_at <= datetime.now(UTC):
            return False

        stmt = (
            pg_insert(ConnectNonce)
            .values(
                nonce=nonce,
                credential_id=credential_id,
                expires_at=expires_at,
                created_by=created_by,
            )
            .on_conflict_do_nothing(index_elements=["nonce"])
        )
        cursor_result: CursorResult[tuple[()]] = await session.execute(stmt)  # type: ignore[assignment]
        return bool(cursor_result.rowcount)

    @staticmethod
    async def register(
        session: AsyncSession,
        *,
        nonce: str,
        credential_id: str,
        expires_at: datetime,
        created_by: str,
    ) -> None:
        """Reserve a flow identifier before its completion can be recorded."""
        session.add(
            ConnectNonce(
                nonce=nonce,
                credential_id=credential_id,
                expires_at=expires_at,
                created_by=created_by,
            )
        )
        await session.flush()

    @staticmethod
    async def mark_completed(session: AsyncSession, *, nonce: str, credential_id: str) -> None:
        """Record completion for the exact registered flow and credential."""
        row = await session.scalar(select(ConnectNonce).where(ConnectNonce.nonce == nonce))
        if row is None:
            raise LookupError("Connect flow identifier is not registered")
        if row.credential_id != credential_id:
            raise LookupError("Connect flow identifier does not match its credential")
        row.completed_at = datetime.now(UTC)
        await session.flush()

    @staticmethod
    async def is_completed(session: AsyncSession, *, nonce: str, credential_id: str) -> bool:
        """Return whether this credential's exact flow committed successfully."""
        result = await session.execute(
            select(ConnectNonce.completed_at).where(
                ConnectNonce.nonce == nonce,
                ConnectNonce.credential_id == credential_id,
            )
        )
        return result.scalar_one_or_none() is not None
