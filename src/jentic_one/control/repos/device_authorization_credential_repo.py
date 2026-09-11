"""Repository for DeviceAuthorizationCredential CRUD operations."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.control.core.schema.device_authorization_credentials import (
    DeviceAuthorizationCredential,
)


class DeviceAuthorizationCredentialRepository:
    """Data access layer for DeviceAuthorizationCredential — flush-only, never commits."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        credential_id: str,
        client_id: str,
        token_url: str,
        authorization_endpoint: str,
        requested_scopes: list[str] | None = None,
        created_by: str | None = None,
    ) -> DeviceAuthorizationCredential:
        row = DeviceAuthorizationCredential(
            id=credential_id,
            client_id=client_id,
            token_url=token_url,
            authorization_endpoint=authorization_endpoint,
            requested_scopes=requested_scopes,
            created_by=created_by,
        )
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    async def get_by_credential(
        session: AsyncSession, credential_id: str
    ) -> DeviceAuthorizationCredential | None:
        stmt = select(DeviceAuthorizationCredential).where(
            DeviceAuthorizationCredential.id == credential_id
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def set_transient_state(
        session: AsyncSession,
        credential_id: str,
        *,
        encrypted_device_code: str,
        user_code: str,
        verification_uri: str,
        verification_uri_complete: str | None,
        poll_interval_seconds: int,
        device_code_expires_at: dt.datetime,
        granted_scopes: list[str],
    ) -> DeviceAuthorizationCredential | None:
        """Populate transient polling state after the vendor returns the device code."""
        row = await DeviceAuthorizationCredentialRepository.get_by_credential(
            session, credential_id
        )
        if row is None:
            return None
        row.encrypted_device_code = encrypted_device_code
        row.user_code = user_code
        row.verification_uri = verification_uri
        row.verification_uri_complete = verification_uri_complete
        row.poll_interval_seconds = poll_interval_seconds
        row.device_code_expires_at = device_code_expires_at
        row.granted_scopes = granted_scopes
        await session.flush()
        return row

    @staticmethod
    async def mark_polled(session: AsyncSession, credential_id: str, when: dt.datetime) -> None:
        row = await DeviceAuthorizationCredentialRepository.get_by_credential(
            session, credential_id
        )
        if row is not None:
            row.last_polled_at = when
            await session.flush()

    @staticmethod
    async def clear_transient(session: AsyncSession, credential_id: str) -> None:
        """Null out polling state on `connected` — the row survives as the
        permanent per-credential registration."""
        row = await DeviceAuthorizationCredentialRepository.get_by_credential(
            session, credential_id
        )
        if row is None:
            return
        row.encrypted_device_code = None
        row.user_code = None
        row.verification_uri = None
        row.verification_uri_complete = None
        row.poll_interval_seconds = None
        row.last_polled_at = None
        row.device_code_expires_at = None
        await session.flush()

    @staticmethod
    async def update_fields(
        session: AsyncSession, credential_id: str, **fields: Any
    ) -> DeviceAuthorizationCredential | None:
        row = await DeviceAuthorizationCredentialRepository.get_by_credential(
            session, credential_id
        )
        if row is None:
            return None
        for key, value in fields.items():
            setattr(row, key, value)
        await session.flush()
        return row
