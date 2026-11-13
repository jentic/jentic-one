"""Repository for AuthorizationCode CRUD."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.authorization_codes import AuthorizationCode


class AuthorizationCodeRepository:
    """Data access layer for AuthorizationCode entities — flush-only, never commits."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        code_hash: str,
        user_id: str,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        scopes: str,
        nonce: str | None,
        expires_at: datetime,
        created_by: str,
        grant_id: str | None = None,
    ) -> AuthorizationCode:
        code = AuthorizationCode(
            code_hash=code_hash,
            user_id=user_id,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            scopes=scopes,
            nonce=nonce,
            grant_id=grant_id,
            expires_at=expires_at,
            created_by=created_by,
        )
        session.add(code)
        await session.flush()
        return code

    @staticmethod
    async def get_by_hash(
        session: AsyncSession, code_hash: str, *, for_update: bool = False
    ) -> AuthorizationCode | None:
        stmt = select(AuthorizationCode).where(AuthorizationCode.code_hash == code_hash)
        if for_update:
            stmt = stmt.with_for_update()
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def consume(session: AsyncSession, code_id: str, consumed_at: datetime) -> None:
        code = await session.get(AuthorizationCode, code_id)
        if code is not None:
            code.consumed_at = consumed_at
            await session.flush()

    @staticmethod
    async def consume_for_user(
        session: AsyncSession, user_id: str, consumed_at: datetime | None = None
    ) -> int:
        """Mark every unredeemed authorization code for ``user_id`` as consumed."""
        stamp = consumed_at if consumed_at is not None else datetime.now(UTC)
        stmt = (
            update(AuthorizationCode)
            .where(AuthorizationCode.user_id == user_id)
            .where(AuthorizationCode.consumed_at.is_(None))
            .values(consumed_at=stamp)
        )
        result = await session.execute(stmt)
        await session.flush()
        return int(result.rowcount)  # type: ignore[attr-defined]

    @staticmethod
    async def delete_by_grant_ids(session: AsyncSession, grant_ids: Sequence[str]) -> int:
        """Delete every code minted for one of ``grant_ids``; returns the row count."""
        if not grant_ids:
            return 0
        result = await session.execute(
            delete(AuthorizationCode).where(AuthorizationCode.grant_id.in_(list(grant_ids)))
        )
        await session.flush()
        return int(result.rowcount)  # type: ignore[attr-defined]
