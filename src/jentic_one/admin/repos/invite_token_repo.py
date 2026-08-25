"""Repository for InviteToken CRUD."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from jentic_one.admin.core.schema.invite_tokens import InviteToken
from jentic_one.admin.services.errors import InviteTokenNotFoundError


class InviteTokenRepository:
    """Data access layer for InviteToken entities — flush-only, never commits."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        user_id: str,
        token_hash: str,
        expires_at: datetime,
        created_by: str,
    ) -> InviteToken:
        token = InviteToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            created_by=created_by,
        )
        session.add(token)
        await session.flush()
        return token

    @staticmethod
    async def get_by_token_hash(session: AsyncSession, token_hash: str) -> InviteToken | None:
        stmt = select(InviteToken).where(InviteToken.token_hash == token_hash)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def redeem(session: AsyncSession, token_id: str) -> InviteToken:
        token = await session.get(InviteToken, token_id)
        if token is None:
            raise InviteTokenNotFoundError(token_id)
        token.redeemed_at = datetime.now(UTC)
        await session.flush()
        return token

    @staticmethod
    async def get_active_for_user(session: AsyncSession, user_id: str) -> list[InviteToken]:
        stmt = (
            select(InviteToken)
            .where(
                InviteToken.user_id == user_id,
                InviteToken.redeemed_at.is_(None),
                InviteToken.expires_at > func.now(),
            )
            .order_by(InviteToken.issued_at.desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def user_ids_with_active_invite(
        session: AsyncSession, user_ids: Sequence[str]
    ) -> set[str]:
        """Return the subset of ``user_ids`` that hold an unredeemed, unexpired invite.

        A single batch query (no N+1) for the roster read path: callers use it to
        derive an "expired" invite state — a still-``pending`` user absent from
        this set has only lapsed/redeemed tokens, so their invite has expired.
        """
        if not user_ids:
            return set()
        stmt = select(InviteToken.user_id).where(
            InviteToken.user_id.in_(user_ids),
            InviteToken.redeemed_at.is_(None),
            InviteToken.expires_at > func.now(),
        )
        result = await session.execute(stmt)
        return {row[0] for row in result}
