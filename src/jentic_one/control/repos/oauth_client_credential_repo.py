"""Repository for OAuthClientCredential CRUD operations."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.control.core.schema.oauth_client_credentials import OAuthClientCredential


class OAuthClientCredentialRepository:
    """Data access layer for OAuthClientCredential entities — flush-only, never commits."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        credential_id: str,
        token_url: str,
        client_id: str,
        encrypted_client_secret: str,
        authorize_url: str | None = None,
        scope: str | None = None,
        created_by: str,
    ) -> OAuthClientCredential:
        row = OAuthClientCredential(
            id=credential_id,
            token_url=token_url,
            client_id=client_id,
            encrypted_client_secret=encrypted_client_secret,
            authorize_url=authorize_url,
            scope=scope,
            created_by=created_by,
        )
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    async def get_by_credential(
        session: AsyncSession, credential_id: str
    ) -> OAuthClientCredential | None:
        return await session.get(OAuthClientCredential, credential_id)

    @staticmethod
    async def update(
        session: AsyncSession,
        credential_id: str,
        *,
        encrypted_client_secret: str | None = None,
        token_url: str | None = None,
        scope: str | None = None,
        clear_scope: bool = False,
    ) -> OAuthClientCredential | None:
        """Apply each provided field independently; ``clear_scope`` drops the stored scope."""
        row = await OAuthClientCredentialRepository.get_by_credential(session, credential_id)
        if row is None:
            return None
        if encrypted_client_secret is not None:
            row.encrypted_client_secret = encrypted_client_secret
        if token_url is not None:
            row.token_url = token_url
        if clear_scope:
            row.scope = None
        elif scope is not None:
            row.scope = scope
        await session.flush()
        return row

    @staticmethod
    async def delete_by_credential(session: AsyncSession, credential_id: str) -> bool:
        row = await OAuthClientCredentialRepository.get_by_credential(session, credential_id)
        if row is None:
            return False
        await session.delete(row)
        await session.flush()
        return True
