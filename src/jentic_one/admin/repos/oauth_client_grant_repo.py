"""Repository for OAuthClientGrant CRUD — flush-only, never commits."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.oauth_client_grants import OAuthClientGrant
from jentic_one.shared.models.oauth_clients import OAuthGrantStatus


class OAuthClientGrantRepository:
    """Data access layer for consent→agent grant rows (phase-3a D3, §4.4)."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        oauth_client_id: str,
        user_id: str,
        agent_id: str,
        scopes: list[str],
        created_by: str | None,
    ) -> OAuthClientGrant:
        grant = OAuthClientGrant(
            oauth_client_id=oauth_client_id,
            user_id=user_id,
            agent_id=agent_id,
            scopes=scopes,
            created_by=created_by,
        )
        session.add(grant)
        await session.flush()
        return grant

    @staticmethod
    async def get_by_id(session: AsyncSession, grant_id: str) -> OAuthClientGrant | None:
        return await session.get(OAuthClientGrant, grant_id)

    @staticmethod
    async def list_active_for_pair(
        session: AsyncSession, *, oauth_client_id: str, agent_id: str
    ) -> list[OAuthClientGrant]:
        """Active grants for one (client, agent) pair — the §4.1 collapse set.

        The consent service revokes every row returned here before inserting
        the fresh grant, so exactly one active row per pair survives while
        grant history is preserved.
        """
        stmt = (
            select(OAuthClientGrant)
            .where(
                OAuthClientGrant.oauth_client_id == oauth_client_id,
                OAuthClientGrant.agent_id == agent_id,
                OAuthClientGrant.status == OAuthGrantStatus.ACTIVE.value,
            )
            .order_by(OAuthClientGrant.created_at.asc(), OAuthClientGrant.id.asc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def revoke(session: AsyncSession, grant_id: str) -> bool:
        """Set the grant revoked. Returns False when already revoked or missing."""
        stmt = (
            update(OAuthClientGrant)
            .where(
                OAuthClientGrant.id == grant_id,
                OAuthClientGrant.status == OAuthGrantStatus.ACTIVE.value,
            )
            .values(status=OAuthGrantStatus.REVOKED.value, revoked_at=datetime.now(UTC))
        )
        result = await session.execute(stmt)
        await session.flush()
        return int(result.rowcount) > 0  # type: ignore[attr-defined]

    @staticmethod
    async def touch_last_used(session: AsyncSession, grant_id: str) -> None:
        """Stamp ``last_used_at`` — called from the token write paths.

        Deliberately NOT called per resolved request: exchange and refresh
        rotation already run in write transactions, so touching there gives
        "last time the client obtained tokens" without turning the read-path
        resolvers into writers.
        """
        stmt = (
            update(OAuthClientGrant)
            .where(OAuthClientGrant.id == grant_id)
            .values(last_used_at=datetime.now(UTC))
        )
        await session.execute(stmt)
        await session.flush()
