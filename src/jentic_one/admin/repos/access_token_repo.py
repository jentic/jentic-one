"""Repository for AccessToken CRUD."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.access_tokens import AccessToken


class AccessTokenRepository:
    """Data access layer for AccessToken entities — flush-only, never commits."""

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
        is_ephemeral: bool = False,
        oauth_client_id: str | None = None,
        oauth_grant_id: str | None = None,
    ) -> AccessToken:
        token = AccessToken(
            token_hash=token_hash,
            actor_id=actor_id,
            actor_type=actor_type,
            scopes=scopes,
            token_family_id=token_family_id,
            expires_at=expires_at,
            created_by=created_by,
            is_ephemeral=is_ephemeral,
            oauth_client_id=oauth_client_id,
            oauth_grant_id=oauth_grant_id,
        )
        session.add(token)
        await session.flush()
        return token

    @staticmethod
    async def get_by_hash(session: AsyncSession, token_hash: str) -> AccessToken | None:
        stmt = select(AccessToken).where(AccessToken.token_hash == token_hash)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def revoke(session: AsyncSession, token_id: str) -> None:
        stmt = (
            update(AccessToken)
            .where(AccessToken.id == token_id)
            .values(revoked_at=datetime.now(UTC))
        )
        await session.execute(stmt)
        await session.flush()

    @staticmethod
    async def revoke_family(session: AsyncSession, token_family_id: str) -> None:
        stmt = (
            update(AccessToken)
            .where(AccessToken.token_family_id == token_family_id)
            .where(AccessToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await session.execute(stmt)
        await session.flush()

    @staticmethod
    async def revoke_by_grant(session: AsyncSession, oauth_grant_id: str) -> int:
        """Revoke every live token minted under one consent grant.

        The grant ``:revoke`` sweep — belt to the resolvers' live grant-gate
        braces. Returns the number of rows revoked.
        """
        stmt = (
            update(AccessToken)
            .where(AccessToken.oauth_grant_id == oauth_grant_id)
            .where(AccessToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        result = await session.execute(stmt)
        await session.flush()
        return int(result.rowcount)  # type: ignore[attr-defined]

    @staticmethod
    async def delete_expired(session: AsyncSession, before: datetime) -> int:
        stmt = delete(AccessToken).where(AccessToken.expires_at < before)
        result = await session.execute(stmt)
        await session.flush()
        return int(result.rowcount)  # type: ignore[attr-defined]
