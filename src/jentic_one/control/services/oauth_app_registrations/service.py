"""Service for admin-managed OAuth app registrations."""

from __future__ import annotations

from jentic_one.control.core.schema.authorization_code_app_registration_details import (
    AuthorizationCodeAppRegistrationDetails,
)
from jentic_one.control.core.schema.device_authorization_app_registration_details import (
    DeviceAuthorizationAppRegistrationDetails,
)
from jentic_one.control.core.schema.oauth_app_registrations import OAuthAppRegistration
from jentic_one.control.repos import OAuthAppRegistrationRepository
from jentic_one.control.repos.oauth_app_registration_repo import (
    FLOW_KIND_AUTH_CODE,
    FLOW_KIND_DEVICE_AUTHORIZATION,
)
from jentic_one.control.services.oauth_app_registrations.errors import (
    InvalidOAuthAppRegistrationInputError,
    OAuthAppRegistrationInUseError,
    OAuthAppRegistrationNotFoundError,
    SecretRotationNotSupportedError,
)
from jentic_one.control.services.oauth_app_registrations.schemas import (
    OAuthAppRegistrationFlowKind,
    OAuthAppRegistrationView,
)
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit_best_effort
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models.audit import AuditReason


class OAuthAppRegistrationService:
    """Admin CRUD for OAuth application registrations."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def create_authorization_code(
        self,
        *,
        name: str,
        api_vendor: str,
        catalog_api_id: str,
        display_name: str,
        client_id: str,
        client_secret: str,
        authorize_url: str,
        token_url: str,
        default_scopes: list[str] | None,
        identity: Identity,
    ) -> OAuthAppRegistrationView:
        encrypted = self._ctx.encryption.encrypt(client_secret)
        async with self._ctx.control_db.transaction() as session:
            registration = await OAuthAppRegistrationRepository.create_authorization_code(
                session,
                name=name,
                api_vendor=api_vendor,
                client_id=client_id,
                encrypted_client_secret=encrypted,
                authorize_url=authorize_url,
                token_url=token_url,
                catalog_api_id=catalog_api_id,
                display_name=display_name,
                default_scopes=default_scopes,
                created_by=identity.sub,
            )
            view = _project(registration, dependent_credential_count=0)

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.CREATE,
            target_type=AuditTargetType.OAUTH_APP_REGISTRATION,
            target_id=view.id,
            actor_type=identity.actor_type.value,
            actor_id=identity.sub,
            after={
                "name": name,
                "api_vendor": api_vendor,
                "catalog_api_id": catalog_api_id,
                "display_name": display_name,
                "flow_kind": FLOW_KIND_AUTH_CODE,
                "client_id": client_id,
            },
            origin=identity.origin.value,
        )
        return view

    async def create_device_authorization(
        self,
        *,
        name: str,
        api_vendor: str,
        catalog_api_id: str,
        display_name: str,
        client_id: str,
        authorization_endpoint: str,
        token_endpoint: str,
        default_scopes: list[str] | None,
        identity: Identity,
    ) -> OAuthAppRegistrationView:
        async with self._ctx.control_db.transaction() as session:
            registration = await OAuthAppRegistrationRepository.create_device_authorization(
                session,
                name=name,
                api_vendor=api_vendor,
                client_id=client_id,
                authorization_endpoint=authorization_endpoint,
                token_endpoint=token_endpoint,
                catalog_api_id=catalog_api_id,
                display_name=display_name,
                default_scopes=default_scopes,
                created_by=identity.sub,
            )
            view = _project(registration, dependent_credential_count=0)

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.CREATE,
            target_type=AuditTargetType.OAUTH_APP_REGISTRATION,
            target_id=view.id,
            actor_type=identity.actor_type.value,
            actor_id=identity.sub,
            after={
                "name": name,
                "api_vendor": api_vendor,
                "catalog_api_id": catalog_api_id,
                "display_name": display_name,
                "flow_kind": FLOW_KIND_DEVICE_AUTHORIZATION,
                "client_id": client_id,
            },
            origin=identity.origin.value,
        )
        return view

    async def get(self, registration_id: str) -> OAuthAppRegistrationView:
        async with self._ctx.control_db.session() as session:
            registration = await OAuthAppRegistrationRepository.get_by_id(session, registration_id)
            if registration is None:
                raise OAuthAppRegistrationNotFoundError(registration_id)
            count = await OAuthAppRegistrationRepository.count_dependent_credentials(
                session, registration_id
            )
        return _project(registration, dependent_credential_count=count)

    async def list_all(
        self,
        *,
        api_vendor: str | None = None,
        include_inactive: bool = False,
    ) -> list[OAuthAppRegistrationView]:
        async with self._ctx.control_db.session() as session:
            rows = await OAuthAppRegistrationRepository.list_all(
                session,
                api_vendor=api_vendor,
                include_inactive=include_inactive,
            )
            counts = {
                row.id: await OAuthAppRegistrationRepository.count_dependent_credentials(
                    session, row.id
                )
                for row in rows
            }
        return [_project(row, dependent_credential_count=counts[row.id]) for row in rows]

    async def update(
        self,
        registration_id: str,
        *,
        name: str | None = None,
        display_name: str | None = None,
        default_scopes: list[str] | None = None,
        authorize_url: str | None = None,
        token_url: str | None = None,
        authorization_endpoint: str | None = None,
        token_endpoint: str | None = None,
        is_active: bool | None = None,
        identity: Identity,
    ) -> OAuthAppRegistrationView:
        async with self._ctx.control_db.transaction() as session:
            registration = await OAuthAppRegistrationRepository.get_by_id(session, registration_id)
            if registration is None:
                raise OAuthAppRegistrationNotFoundError(registration_id)

            before = _snapshot(registration)

            if name is not None or display_name is not None or is_active is not None:
                await OAuthAppRegistrationRepository.update_base(
                    session,
                    registration_id,
                    name=name,
                    display_name=display_name,
                    is_active=is_active,
                )

            if registration.flow_kind == FLOW_KIND_AUTH_CODE:
                if authorization_endpoint is not None or token_endpoint is not None:
                    raise InvalidOAuthAppRegistrationInputError(
                        "authorization_endpoint / token_endpoint are only valid on "
                        "device-authorization registrations"
                    )
                if any(v is not None for v in (authorize_url, token_url, default_scopes)):
                    await OAuthAppRegistrationRepository.update_authorization_code_details(
                        session,
                        registration_id,
                        authorize_url=authorize_url,
                        token_url=token_url,
                        default_scopes=default_scopes,
                    )
            else:
                if authorize_url is not None or token_url is not None:
                    raise InvalidOAuthAppRegistrationInputError(
                        "authorize_url / token_url are only valid on authorization-code "
                        "registrations"
                    )
                if any(
                    v is not None for v in (authorization_endpoint, token_endpoint, default_scopes)
                ):
                    await OAuthAppRegistrationRepository.update_device_authorization_details(
                        session,
                        registration_id,
                        authorization_endpoint=authorization_endpoint,
                        token_endpoint=token_endpoint,
                        default_scopes=default_scopes,
                    )

            # Explicitly refresh the base row + the two extension relationships.
            # A bare ``session.refresh(registration)`` in async can trip
            # ``MissingGreenlet`` when the projector touches an expired
            # relationship — refreshing the attribute names up front loads
            # them synchronously via a wrapped await.
            await session.refresh(
                registration,
                attribute_names=[
                    "authorization_code_details",
                    "device_authorization_details",
                ],
            )
            count = await OAuthAppRegistrationRepository.count_dependent_credentials(
                session, registration_id
            )
            view = _project(registration, dependent_credential_count=count)
            after = _snapshot(registration)

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.UPDATE,
            target_type=AuditTargetType.OAUTH_APP_REGISTRATION,
            target_id=registration_id,
            actor_type=identity.actor_type.value,
            actor_id=identity.sub,
            before=before,
            after=after,
            origin=identity.origin.value,
        )
        return view

    async def rotate_client_secret(
        self,
        registration_id: str,
        *,
        client_secret: str,
        identity: Identity,
    ) -> OAuthAppRegistrationView:
        async with self._ctx.control_db.transaction() as session:
            registration = await OAuthAppRegistrationRepository.get_by_id(session, registration_id)
            if registration is None:
                raise OAuthAppRegistrationNotFoundError(registration_id)
            if registration.flow_kind != FLOW_KIND_AUTH_CODE:
                raise SecretRotationNotSupportedError(registration_id)

            encrypted = self._ctx.encryption.encrypt(client_secret)
            details = await OAuthAppRegistrationRepository.rotate_client_secret(
                session,
                registration_id,
                encrypted_client_secret=encrypted,
            )
            if details is None:
                raise OAuthAppRegistrationNotFoundError(registration_id)

            count = await OAuthAppRegistrationRepository.count_dependent_credentials(
                session, registration_id
            )
            view = _project(registration, dependent_credential_count=count)

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.ROTATE,
            target_type=AuditTargetType.OAUTH_APP_REGISTRATION,
            target_id=registration_id,
            actor_type=identity.actor_type.value,
            actor_id=identity.sub,
            reason=AuditReason.CLIENT_SECRET_ROTATED.value,
            origin=identity.origin.value,
        )
        return view

    async def delete(self, registration_id: str, *, identity: Identity) -> None:
        async with self._ctx.control_db.transaction() as session:
            registration = await OAuthAppRegistrationRepository.get_by_id(session, registration_id)
            if registration is None:
                raise OAuthAppRegistrationNotFoundError(registration_id)
            count = await OAuthAppRegistrationRepository.count_dependent_credentials(
                session, registration_id
            )
            if count > 0:
                raise OAuthAppRegistrationInUseError(registration_id, count)

            before = _snapshot(registration)
            await OAuthAppRegistrationRepository.delete(session, registration_id)

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.DELETE,
            target_type=AuditTargetType.OAUTH_APP_REGISTRATION,
            target_id=registration_id,
            actor_type=identity.actor_type.value,
            actor_id=identity.sub,
            before=before,
            origin=identity.origin.value,
        )


def _project(
    registration: OAuthAppRegistration, *, dependent_credential_count: int
) -> OAuthAppRegistrationView:
    ac_details: AuthorizationCodeAppRegistrationDetails | None = (
        registration.authorization_code_details
    )
    dev_details: DeviceAuthorizationAppRegistrationDetails | None = (
        registration.device_authorization_details
    )
    return OAuthAppRegistrationView(
        id=registration.id,
        name=registration.name,
        api_vendor=registration.api_vendor,
        catalog_api_id=registration.catalog_api_id,
        display_name=registration.display_name,
        flow_kind=OAuthAppRegistrationFlowKind(registration.flow_kind),
        client_id=registration.client_id,
        is_active=registration.is_active,
        has_client_secret=ac_details is not None,
        secret_last_rotated_at=(
            ac_details.secret_last_rotated_at if ac_details is not None else None
        ),
        authorize_url=ac_details.authorize_url if ac_details is not None else None,
        token_url=ac_details.token_url if ac_details is not None else None,
        authorization_endpoint=(
            dev_details.authorization_endpoint if dev_details is not None else None
        ),
        token_endpoint=(dev_details.token_endpoint if dev_details is not None else None),
        default_scopes=(
            ac_details.default_scopes
            if ac_details is not None
            else (dev_details.default_scopes if dev_details is not None else None)
        ),
        created_at=registration.created_at,
        updated_at=registration.updated_at,
        created_by=registration.created_by,
        dependent_credential_count=dependent_credential_count,
    )


def _snapshot(registration: OAuthAppRegistration) -> dict[str, object]:
    ac: AuthorizationCodeAppRegistrationDetails | None = registration.authorization_code_details
    dev: DeviceAuthorizationAppRegistrationDetails | None = (
        registration.device_authorization_details
    )
    snap: dict[str, object] = {
        "name": registration.name,
        "api_vendor": registration.api_vendor,
        "catalog_api_id": registration.catalog_api_id,
        "display_name": registration.display_name,
        "flow_kind": registration.flow_kind,
        "client_id": registration.client_id,
        "is_active": registration.is_active,
    }
    if ac is not None:
        snap["authorize_url"] = ac.authorize_url
        snap["token_url"] = ac.token_url
        snap["default_scopes"] = ac.default_scopes
    if dev is not None:
        snap["authorization_endpoint"] = dev.authorization_endpoint
        snap["token_endpoint"] = dev.token_endpoint
        snap["default_scopes"] = dev.default_scopes
    return snap
