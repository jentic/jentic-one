"""Repository for OAuthToken CRUD operations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.control.core.schema.oauth_tokens import OAuthToken

ExpiryEventKind = Literal["expiring_soon", "expired"]


class OAuthTokenRepository:
    """Data access layer for OAuthToken entities — flush-only, never commits."""

    @staticmethod
    async def acquire_refresh_lock(session: AsyncSession, credential_id: str) -> None:
        """Acquire a transaction-scoped advisory lock for refresh single-flight.

        On PostgreSQL this uses pg_advisory_xact_lock (released at commit/rollback).
        On SQLite this is a no-op — the in-process asyncio.Lock handles concurrency.
        """
        dialect = session.bind.dialect.name if session.bind else "sqlite"
        if dialect == "postgresql":
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:cid))"),
                {"cid": credential_id},
            )

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        credential_id: str,
        encrypted_access_token: str,
        encrypted_refresh_token: str | None = None,
        expires_at: datetime | None = None,
        scope: str | None = None,
        app_registration_id: str | None = None,
        platform_application_id: str | None = None,
        issued_to_user: str | None = None,
        created_by: str,
    ) -> OAuthToken:
        row = OAuthToken(
            credential_id=credential_id,
            encrypted_access_token=encrypted_access_token,
            encrypted_refresh_token=encrypted_refresh_token,
            expires_at=expires_at,
            scope=scope,
            app_registration_id=app_registration_id,
            platform_application_id=platform_application_id,
            issued_to_user=issued_to_user,
            created_by=created_by,
        )
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    async def get_by_credential(session: AsyncSession, credential_id: str) -> OAuthToken | None:
        result = await session.execute(
            select(OAuthToken).where(OAuthToken.credential_id == credential_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def update_tokens(
        session: AsyncSession,
        credential_id: str,
        *,
        encrypted_access_token: str,
        encrypted_refresh_token: str | None = None,
        expires_at: datetime | None = None,
        scope: str | None = None,
    ) -> OAuthToken | None:
        row = await OAuthTokenRepository.get_by_credential(session, credential_id)
        if row is None:
            return None
        row.encrypted_access_token = encrypted_access_token
        if encrypted_refresh_token is not None:
            row.encrypted_refresh_token = encrypted_refresh_token
        row.expires_at = expires_at
        if scope is not None:
            row.scope = scope
        # Fresh tokens supersede any past revocation: a re-connect over a
        # previously revoked row must yield a live token, not a permanently
        # revoked one (the derived `connected` flag reads `revoked_at`).
        row.revoked_at = None
        row.last_refreshed = datetime.now(UTC)
        await session.flush()
        return row

    @staticmethod
    async def mark_revoked(session: AsyncSession, credential_id: str) -> OAuthToken | None:
        row = await OAuthTokenRepository.get_by_credential(session, credential_id)
        if row is None:
            return None
        row.revoked_at = datetime.now(UTC)
        await session.flush()
        return row

    @staticmethod
    async def list_expiry_candidates(
        session: AsyncSession,
        *,
        now: datetime,
        window_end: datetime,
        limit: int = 100,
    ) -> list[OAuthToken]:
        """Return tokens needing a ``credential.expiring_soon``/``expired`` event.

        A token is a candidate when it is not revoked, has an ``expires_at``, and
        is in one of two un-marked states:

        - already past ``expires_at`` with no ``expired_event_at`` stamp, or
        - within the warning window (``now < expires_at <= window_end``) with no
          ``expiring_soon_event_at`` stamp.

        ``with_for_update(skip_locked=True)`` lets multiple worker replicas sweep
        concurrently without contending on the same rows (a no-op on SQLite).
        """
        expired = and_(
            OAuthToken.expires_at <= now,
            OAuthToken.expired_event_at.is_(None),
        )
        expiring_soon = and_(
            OAuthToken.expires_at > now,
            OAuthToken.expires_at <= window_end,
            OAuthToken.expiring_soon_event_at.is_(None),
        )
        stmt = (
            select(OAuthToken)
            .where(
                OAuthToken.revoked_at.is_(None),
                OAuthToken.expires_at.is_not(None),
                or_(expired, expiring_soon),
            )
            .order_by(OAuthToken.expires_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def mark_expiry_event_emitted(
        session: AsyncSession,
        token: OAuthToken,
        *,
        kind: ExpiryEventKind,
        at: datetime,
    ) -> None:
        """Stamp the dedup marker for the emitted expiry event ``kind``."""
        if kind == "expired":
            token.expired_event_at = at
        else:
            token.expiring_soon_event_at = at
        await session.flush()
