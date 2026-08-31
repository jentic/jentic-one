"""Repository for RefreshToken CRUD."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.refresh_tokens import RefreshToken


class RefreshTokenRepository:
    """Data access layer for RefreshToken entities — flush-only, never commits."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        token_hash: str,
        actor_id: str,
        actor_type: str,
        scopes: list[str],
        token_family_id: str,
        expires_at: datetime,
        created_by: str,
        oauth_client_id: str | None = None,
    ) -> RefreshToken:
        token = RefreshToken(
            token_hash=token_hash,
            actor_id=actor_id,
            actor_type=actor_type,
            scopes=scopes,
            token_family_id=token_family_id,
            expires_at=expires_at,
            created_by=created_by,
            oauth_client_id=oauth_client_id,
        )
        session.add(token)
        await session.flush()
        return token

    @staticmethod
    async def get_by_hash(
        session: AsyncSession, token_hash: str, *, for_update: bool = False
    ) -> RefreshToken | None:
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        if for_update:
            stmt = stmt.with_for_update()
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def consume(session: AsyncSession, token_id: str, *, replaced_by_id: str) -> None:
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.id == token_id)
            .values(consumed_at=datetime.now(UTC), replaced_by_id=replaced_by_id)
        )
        await session.execute(stmt)
        await session.flush()

    @staticmethod
    async def revoke_family(session: AsyncSession, token_family_id: str) -> None:
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.token_family_id == token_family_id)
            .where(RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await session.execute(stmt)
        await session.flush()

    @staticmethod
    async def delete_expired(session: AsyncSession, before: datetime) -> int:
        stmt = delete(RefreshToken).where(RefreshToken.expires_at < before)
        result = await session.execute(stmt)
        await session.flush()
        return int(result.rowcount)  # type: ignore[attr-defined]
