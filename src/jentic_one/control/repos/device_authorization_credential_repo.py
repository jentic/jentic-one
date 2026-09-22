"""Repository for DeviceAuthorizationCredential CRUD operations."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import and_, or_, select, update
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
    async def try_claim_poll_slot(
        session: AsyncSession,
        credential_id: str,
        *,
        now: dt.datetime,
        min_last_polled_at: dt.datetime,
    ) -> bool:
        """Atomic RFC 8628 poll-interval lease.

        Returns ``True`` iff this call won the right to poll the vendor for
        this credential *now*. The check + stamp happen inside one SQL
        ``UPDATE`` so two concurrent scanner replicas cannot both pass the
        interval guard and double-poll the vendor — the loser sees zero
        rows updated and its caller returns ``pending`` for this tick.

        The caller computes ``min_last_polled_at = now - poll_interval``
        from the currently-loaded row and passes it in — that keeps the
        ``UPDATE`` predicate a pure column-vs-value comparison so it stays
        cross-dialect (Postgres + SQLite) without dialect-specific
        interval arithmetic in the SQL. The row's own
        ``poll_interval_seconds`` still gets rechecked as ``IS NOT NULL``
        so a not-yet-populated row cannot be poll-leased.

        Stamping ``last_polled_at`` BEFORE the vendor call (rather than
        after, as ``mark_polled`` used to) means a vendor HTTP error
        doesn't reset the throttle — the retry still respects the interval.
        """
        stmt = (
            update(DeviceAuthorizationCredential)
            .where(
                and_(
                    DeviceAuthorizationCredential.id == credential_id,
                    DeviceAuthorizationCredential.encrypted_device_code.is_not(None),
                    DeviceAuthorizationCredential.poll_interval_seconds.is_not(None),
                    or_(
                        DeviceAuthorizationCredential.last_polled_at.is_(None),
                        DeviceAuthorizationCredential.last_polled_at <= min_last_polled_at,
                    ),
                )
            )
            .values(last_polled_at=now)
            .execution_options(synchronize_session=False)
        )
        # ``session.execute(update(...))`` returns a ``CursorResult`` whose
        # ``rowcount`` reports the affected-row count for the DML — mypy
        # infers the plain ``Result`` supertype (which has no ``rowcount``)
        # so pull it off with ``getattr`` rather than sprinkling ignores.
        result = await session.execute(stmt)
        rowcount: int = getattr(result, "rowcount", 0) or 0
        return rowcount > 0

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
