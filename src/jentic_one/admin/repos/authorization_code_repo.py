"""Repository for AuthorizationCode CRUD."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
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
