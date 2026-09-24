"""Repository for OAuthAppRegistration CRUD operations.

Handles the class-table inheritance shape: every registration has a base row
plus exactly one extension row (auth-code or device-flow). Callers stay
insulated from that split — ``create_authorization_code`` /
``create_device_authorization`` compose both writes in one flush, and reads
eager-load the extension via ``selectin`` (declared on the ORM model).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import ColumnElement, and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.control.core.schema.authorization_code_app_registration_details import (
    AuthorizationCodeAppRegistrationDetails,
)
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.device_authorization_app_registration_details import (
    DeviceAuthorizationAppRegistrationDetails,
)
from jentic_one.control.core.schema.oauth_app_registrations import OAuthAppRegistration

FLOW_KIND_AUTH_CODE = "authorization_code"
FLOW_KIND_DEVICE_AUTHORIZATION = "device_authorization"


class OAuthAppRegistrationRepository:
    """Data access for OAuth app registrations — flush-only, never commits."""

    @staticmethod
    async def create_authorization_code(
        session: AsyncSession,
        *,
        name: str,
        api_vendor: str,
        client_id: str,
        encrypted_client_secret: str,
        authorize_url: str,
        token_url: str,
        default_scopes: list[str] | None = None,
        created_by: str,
    ) -> OAuthAppRegistration:
        registration = OAuthAppRegistration(
            name=name,
            api_vendor=api_vendor,
            flow_kind=FLOW_KIND_AUTH_CODE,
            client_id=client_id,
            is_active=True,
            created_by=created_by,
        )
        session.add(registration)
        await session.flush()
        details = AuthorizationCodeAppRegistrationDetails(
            id=registration.id,
            encrypted_client_secret=encrypted_client_secret,
            authorize_url=authorize_url,
            token_url=token_url,
            default_scopes=default_scopes,
            created_by=created_by,
        )
        session.add(details)
        await session.flush()
        # Wire both extension relationships onto the parent explicitly so the
        # async caller can project without triggering an implicit lazy-load
        # (which needs a greenlet-wrapped await under the asyncpg driver).
        # The "other" relationship is None by construction for an auth-code
        # registration, so pin it now to mark the attribute as loaded.
        registration.authorization_code_details = details
        registration.device_authorization_details = None
        return registration

    @staticmethod
    async def create_device_authorization(
        session: AsyncSession,
        *,
        name: str,
        api_vendor: str,
        client_id: str,
        authorization_endpoint: str,
        token_endpoint: str,
        default_scopes: list[str] | None = None,
        created_by: str,
    ) -> OAuthAppRegistration:
        registration = OAuthAppRegistration(
            name=name,
            api_vendor=api_vendor,
            flow_kind=FLOW_KIND_DEVICE_AUTHORIZATION,
            client_id=client_id,
            is_active=True,
            created_by=created_by,
        )
        session.add(registration)
        await session.flush()
        details = DeviceAuthorizationAppRegistrationDetails(
            id=registration.id,
            authorization_endpoint=authorization_endpoint,
            token_endpoint=token_endpoint,
            default_scopes=default_scopes,
            created_by=created_by,
        )
        session.add(details)
        await session.flush()
        # Wire both extension relationships onto the parent explicitly (see
        # ``create_authorization_code`` for the rationale).
        registration.device_authorization_details = details
        registration.authorization_code_details = None
        return registration

    @staticmethod
    async def get_by_id(session: AsyncSession, registration_id: str) -> OAuthAppRegistration | None:
        return await session.get(OAuthAppRegistration, registration_id)

    @staticmethod
    async def list_all(
        session: AsyncSession,
        *,
        api_vendor: str | None = None,
        include_inactive: bool = False,
        filters: list[ColumnElement[bool]] | None = None,
    ) -> list[OAuthAppRegistration]:
        stmt = select(OAuthAppRegistration).order_by(OAuthAppRegistration.created_at.desc())
        if api_vendor is not None:
            stmt = stmt.where(OAuthAppRegistration.api_vendor == api_vendor)
        if not include_inactive:
            stmt = stmt.where(OAuthAppRegistration.is_active.is_(True))
        if filters:
            stmt = stmt.where(and_(*filters))
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_preferred_for_vendor(
        session: AsyncSession,
        *,
        api_vendor: str,
        flow_kind: str | None = None,
    ) -> OAuthAppRegistration | None:
        """Pick the most-recently-updated active registration for a vendor.

        Used by connect-time resolution when the caller asks for a vendor slug
        rather than a specific registration id. Callers with a specific
        preferred flow may pin ``flow_kind``; otherwise auth-code is preferred
        over device flow (auth-code is the newer, browser-driven path).
        """
        stmt = (
            select(OAuthAppRegistration)
            .where(
                OAuthAppRegistration.api_vendor == api_vendor,
                OAuthAppRegistration.is_active.is_(True),
            )
            .order_by(OAuthAppRegistration.updated_at.desc())
        )
        if flow_kind is not None:
            stmt = stmt.where(OAuthAppRegistration.flow_kind == flow_kind)
        result = await session.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def update_base(
        session: AsyncSession,
        registration_id: str,
        *,
        name: str | None = None,
        is_active: bool | None = None,
    ) -> OAuthAppRegistration | None:
        row = await OAuthAppRegistrationRepository.get_by_id(session, registration_id)
        if row is None:
            return None
        if name is not None:
            row.name = name
        if is_active is not None:
            row.is_active = is_active
        await session.flush()
        return row

    @staticmethod
    async def update_authorization_code_details(
        session: AsyncSession,
        registration_id: str,
        *,
        authorize_url: str | None = None,
        token_url: str | None = None,
        default_scopes: list[str] | None = None,
    ) -> AuthorizationCodeAppRegistrationDetails | None:
        row = await session.get(AuthorizationCodeAppRegistrationDetails, registration_id)
        if row is None:
            return None
        if authorize_url is not None:
            row.authorize_url = authorize_url
        if token_url is not None:
            row.token_url = token_url
        if default_scopes is not None:
            row.default_scopes = default_scopes
        await session.flush()
        return row

    @staticmethod
    async def update_device_authorization_details(
        session: AsyncSession,
        registration_id: str,
        *,
        authorization_endpoint: str | None = None,
        token_endpoint: str | None = None,
        default_scopes: list[str] | None = None,
    ) -> DeviceAuthorizationAppRegistrationDetails | None:
        row = await session.get(DeviceAuthorizationAppRegistrationDetails, registration_id)
        if row is None:
            return None
        if authorization_endpoint is not None:
            row.authorization_endpoint = authorization_endpoint
        if token_endpoint is not None:
            row.token_endpoint = token_endpoint
        if default_scopes is not None:
            row.default_scopes = default_scopes
        await session.flush()
        return row

    @staticmethod
    async def rotate_client_secret(
        session: AsyncSession,
        registration_id: str,
        *,
        encrypted_client_secret: str,
    ) -> AuthorizationCodeAppRegistrationDetails | None:
        row = await session.get(AuthorizationCodeAppRegistrationDetails, registration_id)
        if row is None:
            return None
        row.encrypted_client_secret = encrypted_client_secret
        row.secret_last_rotated_at = dt.datetime.now(dt.UTC)
        await session.flush()
        return row

    @staticmethod
    async def has_dependent_credentials(session: AsyncSession, registration_id: str) -> bool:
        stmt = select(exists().where(Credential.oauth_app_registration_id == registration_id))
        result = await session.execute(stmt)
        return bool(result.scalar())

    @staticmethod
    async def count_dependent_credentials(session: AsyncSession, registration_id: str) -> int:
        stmt = select(func.count(Credential.id)).where(
            Credential.oauth_app_registration_id == registration_id
        )
        result = await session.execute(stmt)
        return int(result.scalar() or 0)

    @staticmethod
    async def delete(session: AsyncSession, registration_id: str) -> bool:
        row = await OAuthAppRegistrationRepository.get_by_id(session, registration_id)
        if row is None:
            return False
        await session.delete(row)
        await session.flush()
        return True
