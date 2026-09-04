"""Repository for OAuthClient CRUD — flush-only, never commits."""

from __future__ import annotations

import hashlib

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.oauth_clients import OAuthClient
from jentic_one.shared.models.oauth_clients import (
    OAuthClientApprovalStatus,
    OAuthConsentModel,
    OAuthRegistrationSource,
    TokenEndpointAuthMethod,
)


def redirect_uris_fingerprint(redirect_uris: list[str]) -> str:
    """SHA-256 hex of the sorted, deduplicated redirect-URI set.

    Order-insensitive and duplicate-insensitive (the list is normalized to a
    sorted set before hashing, matching the set comparison the DCR service's
    dedupe re-verify performs) and exact — any added, removed, or altered URI
    changes the fingerprint. Together with ``software_id`` this is the D8
    dedupe key for DCR registrations. URIs cannot contain raw newlines (they
    are validated URLs), so the newline join is unambiguous.
    """
    return hashlib.sha256("\n".join(sorted(set(redirect_uris))).encode()).hexdigest()


class OAuthClientRepository:
    """Data access layer for OAuthClient entities."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        client_id: str,
        name: str,
        redirect_uris: list[str],
        client_secret_hash: str | None,
        description: str | None = None,
        require_consent: bool = True,
        allowed_scopes: list[str] | None = None,
        token_endpoint_auth_method: str = TokenEndpointAuthMethod.CLIENT_SECRET_BASIC.value,
        consent_model: str = OAuthConsentModel.USER.value,
        registration_source: str = OAuthRegistrationSource.ADMIN.value,
        software_id: str | None = None,
        approval_status: str = OAuthClientApprovalStatus.APPROVED.value,
        active: bool = True,
        created_by: str | None,
    ) -> OAuthClient:
        """Create a new OAuth client. ``client_secret_hash=None`` means a public client."""
        client = OAuthClient(
            client_id=client_id,
            client_secret_hash=client_secret_hash,
            name=name,
            description=description,
            redirect_uris=redirect_uris,
            redirect_uris_fingerprint=redirect_uris_fingerprint(redirect_uris),
            require_consent=require_consent,
            allowed_scopes=allowed_scopes,
            token_endpoint_auth_method=token_endpoint_auth_method,
            consent_model=consent_model,
            registration_source=registration_source,
            software_id=software_id,
            approval_status=approval_status,
            active=active,
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
    async def list_by_client_ids(session: AsyncSession, client_ids: list[str]) -> list[OAuthClient]:
        """Fetch client rows for a set of public ``client_id`` strings.

        Used to enrich grant listings (client name + redirect-URI
        origin) without an N+1 per grant row.
        """
        if not client_ids:
            return []
        stmt = select(OAuthClient).where(OAuthClient.client_id.in_(client_ids))
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def list_dcr_by_dedupe_key(
        session: AsyncSession, software_id: str, fingerprint: str
    ) -> list[OAuthClient]:
        """List DCR rows matching the D8 dedupe key, via its covering index.

        Hits the non-unique ``(software_id, redirect_uris_fingerprint)`` index
        instead of scanning every row claiming the ``software_id``.
        Callers must still verify the exact redirect-URI set on the returned
        rows — the fingerprint is a hash, so equality is necessary evidence,
        not proof (collision guard).

        Oldest first as a stable base ordering. The caller picks the dedupe
        winner: the service prefers approved > pending > denied among exact
        matches (a double-register race can leave several rows, and the admin
        may have approved a newer one), falling back to the oldest row within
        the same status.
        """
        stmt = (
            select(OAuthClient)
            .where(
                OAuthClient.software_id == software_id,
                OAuthClient.redirect_uris_fingerprint == fingerprint,
                OAuthClient.registration_source == OAuthRegistrationSource.DCR.value,
            )
            .order_by(OAuthClient.created_at.asc(), OAuthClient.id.asc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def list_all(
        session: AsyncSession,
        *,
        include_inactive: bool = False,
        approval_status: str | None = None,
    ) -> list[OAuthClient]:
        stmt = select(OAuthClient).order_by(OAuthClient.created_at.desc())
        if not include_inactive:
            stmt = stmt.where(OAuthClient.active.is_(True))
        if approval_status is not None:
            stmt = stmt.where(OAuthClient.approval_status == approval_status)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def set_approval_status(
        session: AsyncSession, id: str, *, approval_status: str, active: bool
    ) -> OAuthClient | None:
        """Set the approval lifecycle state (and the coupled active flag).

        Returns the updated client, or None if not found.
        """
        client = await session.get(OAuthClient, id)
        if client is None:
            return None
        client.approval_status = approval_status
        client.active = active
        await session.flush()
        return client

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
        reset_allowed_scopes: bool = False,
    ) -> OAuthClient | None:
        """Update an OAuth client. Returns None if not found.

        ``reset_allowed_scopes=True`` sets the column to NULL (unrestricted) and
        takes precedence over ``allowed_scopes``.
        """
        client = await session.get(OAuthClient, id)
        if client is None:
            return None

        if name is not None:
            client.name = name
        if description is not None:
            client.description = description
        if redirect_uris is not None:
            client.redirect_uris = redirect_uris
            client.redirect_uris_fingerprint = redirect_uris_fingerprint(redirect_uris)
        if active is not None:
            client.active = active
        if require_consent is not None:
            client.require_consent = require_consent
        if reset_allowed_scopes:
            client.allowed_scopes = None
        elif allowed_scopes is not None:
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
