"""Repository for OAuthClient CRUD — flush-only, never commits."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.oauth_clients import OAuthClient


class OAuthClientRepository:
    """Data access layer for OAuthClient entities."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        client_id: str,
        name: str,
        redirect_uris: list[str],
        client_secret_hash: str,
        description: str | None = None,
        require_consent: bool = True,
        allowed_scopes: list[str] | None = None,
        created_by: str | None,
    ) -> OAuthClient:
        """Create a new OAuth client."""
        client = OAuthClient(
            client_id=client_id,
            client_secret_hash=client_secret_hash,
            name=name,
            description=description,
            redirect_uris=redirect_uris,
            require_consent=require_consent,
            allowed_scopes=allowed_scopes,
            created_by=created_by,
        )
        session.add(client)
        await session.flush()
        return client

    @staticmethod
    async def update_secret_hash(
        session: AsyncSession, id: str, secret_hash: str
    ) -> OAuthClient | None:
        """Update the client_secret_hash for secret rotation. Returns None if not found."""
        client = await session.get(OAuthClient, id)
        if client is None:
            return None
        client.client_secret_hash = secret_hash
        await session.flush()
        return client

    @staticmethod
    async def get_by_id(session: AsyncSession, id: str) -> OAuthClient | None:
        return await session.get(OAuthClient, id)

    @staticmethod
    async def get_by_client_id(session: AsyncSession, client_id: str) -> OAuthClient | None:
        stmt = select(OAuthClient).where(OAuthClient.client_id == client_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(
        session: AsyncSession, *, include_inactive: bool = False
    ) -> list[OAuthClient]:
        stmt = select(OAuthClient).order_by(OAuthClient.created_at.desc())
        if not include_inactive:
            stmt = stmt.where(OAuthClient.active.is_(True))
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def update(
        session: AsyncSession,
        id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        redirect_uris: list[str] | None = None,
        active: bool | None = None,
        require_consent: bool | None = None,
        allowed_scopes: list[str] | None = None,
    ) -> OAuthClient | None:
        """Update an OAuth client. Returns None if not found."""
        client = await session.get(OAuthClient, id)
        if client is None:
            return None

        if name is not None:
            client.name = name
        if description is not None:
            client.description = description
        if redirect_uris is not None:
            client.redirect_uris = redirect_uris
        if active is not None:
            client.active = active
        if require_consent is not None:
            client.require_consent = require_consent
        if allowed_scopes is not None:
            client.allowed_scopes = allowed_scopes

        await session.flush()
        return client

    @staticmethod
    async def deactivate(session: AsyncSession, id: str) -> bool:
        """Soft-delete by setting active=False. Returns True if updated."""
        stmt = update(OAuthClient).where(OAuthClient.id == id).values(active=False)
        result = await session.execute(stmt)
        await session.flush()
        return int(result.rowcount) > 0  # type: ignore[attr-defined]
