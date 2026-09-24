"""Repository for Sigv4Credential CRUD operations."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.control.core.schema.sigv4_credentials import Sigv4Credential


class Sigv4CredentialRepository:
    """Data access layer for Sigv4Credential entities — flush-only, never commits."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        credential_id: str,
        access_key_id: str,
        encrypted_secret_access_key: str,
        secret_preview: str | None,
        encrypted_session_token: str | None,
        region: str,
        service: str,
        created_by: str,
    ) -> Sigv4Credential:
        row = Sigv4Credential(
            credential_id=credential_id,
            access_key_id=access_key_id,
            encrypted_secret_access_key=encrypted_secret_access_key,
            secret_preview=secret_preview,
            encrypted_session_token=encrypted_session_token,
            region=region,
            service=service,
            created_by=created_by,
        )
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    async def get_by_credential(
        session: AsyncSession, credential_id: str
    ) -> Sigv4Credential | None:
        result = await session.execute(
            select(Sigv4Credential).where(Sigv4Credential.credential_id == credential_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update(
        session: AsyncSession,
        credential_id: str,
        *,
        access_key_id: str | None = None,
        encrypted_secret_access_key: str | None = None,
        secret_preview: str | None = None,
        encrypted_session_token: str | None = None,
        clear_session_token: bool = False,
        region: str | None = None,
        service: str | None = None,
    ) -> Sigv4Credential | None:
        """Rotate/edit signing material.

        A keypair rotation supplies both ``access_key_id`` and
        ``encrypted_secret_access_key`` together. ``clear_session_token``
        explicitly drops a stored session token (distinct from "leave unchanged").
        """
        row = await Sigv4CredentialRepository.get_by_credential(session, credential_id)
        if row is None:
            return None
        if access_key_id is not None:
            row.access_key_id = access_key_id
        if encrypted_secret_access_key is not None:
            row.encrypted_secret_access_key = encrypted_secret_access_key
            row.secret_preview = secret_preview
        if clear_session_token:
            row.encrypted_session_token = None
        elif encrypted_session_token is not None:
            row.encrypted_session_token = encrypted_session_token
        if region is not None:
            row.region = region
        if service is not None:
            row.service = service
        await session.flush()
        return row

    @staticmethod
    async def delete_by_credential(session: AsyncSession, credential_id: str) -> bool:
        row = await Sigv4CredentialRepository.get_by_credential(session, credential_id)
        if row is None:
            return False
        await session.delete(row)
        await session.flush()
        return True
