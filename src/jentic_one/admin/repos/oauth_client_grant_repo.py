"""Repository for OAuthClientGrant CRUD — flush-only, never commits."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select, update
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
    async def list_active_for_agent(session: AsyncSession, agent_id: str) -> list[OAuthClientGrant]:
        """Every active grant bound to ONE agent — the transfer sweep set (G10, #1222).

        An agent ownership transfer revokes all of these in the transfer's own
        transaction, so unlike :meth:`list_grants` this is deliberately
        unpaginated: the sweep must see the complete set or fail the transfer.
        """
        stmt = (
            select(OAuthClientGrant)
            .where(
                OAuthClientGrant.agent_id == agent_id,
                OAuthClientGrant.status == OAuthGrantStatus.ACTIVE.value,
            )
            .order_by(OAuthClientGrant.created_at.asc(), OAuthClientGrant.id.asc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def list_grants(
        session: AsyncSession,
        *,
        agent_id: str | None = None,
        oauth_client_id: str | None = None,
        user_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        cursor_created_at: datetime | None = None,
        cursor_id: str | None = None,
    ) -> list[OAuthClientGrant]:
        """List grant rows for the §4.8 surfaces, newest first.

        Filterable by any combination of agent, client (public ``client_id``
        string — the token-lineage join key, D3), consenting user, and status.
        Compound keyset on ``(created_at, id)`` matching the order-by, like
        ``OverlayRepository.list_page``: the id tiebreaker keeps rows sharing
        a boundary ``created_at`` (burst consents, coarse timestamp
        precision) from being skipped between pages.
        """
        stmt = (
            select(OAuthClientGrant)
            .order_by(OAuthClientGrant.created_at.desc(), OAuthClientGrant.id.desc())
            .limit(limit)
        )
        if agent_id is not None:
            stmt = stmt.where(OAuthClientGrant.agent_id == agent_id)
        if oauth_client_id is not None:
            stmt = stmt.where(OAuthClientGrant.oauth_client_id == oauth_client_id)
        if user_id is not None:
            stmt = stmt.where(OAuthClientGrant.user_id == user_id)
        if status is not None:
            stmt = stmt.where(OAuthClientGrant.status == status)
        if cursor_created_at is not None and cursor_id is not None:
            stmt = stmt.where(
                or_(
                    OAuthClientGrant.created_at < cursor_created_at,
                    and_(
                        OAuthClientGrant.created_at == cursor_created_at,
                        OAuthClientGrant.id < cursor_id,
                    ),
                )
            )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def count_active_by_client(session: AsyncSession) -> dict[str, int]:
        """Active-grant counts keyed by public ``client_id`` (§4.8 per-client count)."""
        stmt = (
            select(OAuthClientGrant.oauth_client_id, func.count())
            .where(OAuthClientGrant.status == OAuthGrantStatus.ACTIVE.value)
            .group_by(OAuthClientGrant.oauth_client_id)
        )
        result = await session.execute(stmt)
        return dict(result.all())  # type: ignore[arg-type]

    @staticmethod
    async def count_active_for_client(session: AsyncSession, oauth_client_id: str) -> int:
        """Active-grant count for ONE public ``client_id``.

        The single-client companion to :meth:`count_active_by_client` — a
        per-client GET must not pay a whole-table aggregate for one row.
        """
        stmt = (
            select(func.count())
            .select_from(OAuthClientGrant)
            .where(
                OAuthClientGrant.oauth_client_id == oauth_client_id,
                OAuthClientGrant.status == OAuthGrantStatus.ACTIVE.value,
            )
        )
        result = await session.execute(stmt)
        return int(result.scalar_one())

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
