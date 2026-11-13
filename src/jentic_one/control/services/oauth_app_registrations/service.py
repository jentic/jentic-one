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
from jentic_one.control.services.credentials.token_endpoint import same_origin
from jentic_one.control.services.oauth_app_registrations.errors import (
    InvalidOAuthAppRegistrationInputError,
    OAuthAppRegistrationInUseError,
    OAuthAppRegistrationNotFoundError,
    SecretRotationNotSupportedError,
)
from jentic_one.control.services.oauth_app_registrations.schemas import (
    AuthorizationCodeRegistrationCreate,
    DeviceAuthorizationRegistrationCreate,
    OAuthAppRegistrationFlowKind,
    OAuthAppRegistrationQuery,
    OAuthAppRegistrationUpdate,
    OAuthAppRegistrationView,
)
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit_best_effort
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import EgressConfig, resolved_control_egress
from jentic_one.shared.context import Context
from jentic_one.shared.models.audit import AuditReason
from jentic_one.shared.pagination import Page, decode_cursor, encode_cursor
from jentic_one.shared.url_validation import validate_upstream_url_async


class OAuthAppRegistrationService:
    """Admin CRUD for OAuth application registrations."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def create_authorization_code(
        self,
        payload: AuthorizationCodeRegistrationCreate,
        *,
        identity: Identity,
    ) -> OAuthAppRegistrationView:
        egress = resolved_control_egress(self._ctx.config)
        authorize_url = await _validate_endpoint_url("authorize_url", payload.authorize_url, egress)
        token_url = await _validate_endpoint_url("token_url", payload.token_url, egress)
        encrypted = self._ctx.encryption.encrypt(payload.client_secret.get_secret_value())
        async with self._ctx.control_db.transaction() as session:
            registration = await OAuthAppRegistrationRepository.create_authorization_code(
                session,
                name=payload.name,
                api_vendor=payload.api_vendor,
                client_id=payload.client_id,
                encrypted_client_secret=encrypted,
                authorize_url=authorize_url,
                token_url=token_url,
                catalog_api_id=payload.catalog_api_id,
                display_name=payload.display_name,
                default_scopes=payload.default_scopes,
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
                "name": payload.name,
                "api_vendor": payload.api_vendor,
                "catalog_api_id": payload.catalog_api_id,
                "display_name": payload.display_name,
                "flow_kind": FLOW_KIND_AUTH_CODE,
                "client_id": payload.client_id,
            },
            origin=identity.origin.value,
        )
        return view

    async def create_device_authorization(
        self,
        payload: DeviceAuthorizationRegistrationCreate,
        *,
        identity: Identity,
    ) -> OAuthAppRegistrationView:
        egress = resolved_control_egress(self._ctx.config)
        authorization_endpoint = await _validate_endpoint_url(
            "authorization_endpoint", payload.authorization_endpoint, egress
        )
        token_endpoint = await _validate_endpoint_url(
            "token_endpoint", payload.token_endpoint, egress
        )
        async with self._ctx.control_db.transaction() as session:
            registration = await OAuthAppRegistrationRepository.create_device_authorization(
                session,
                name=payload.name,
                api_vendor=payload.api_vendor,
                client_id=payload.client_id,
                authorization_endpoint=authorization_endpoint,
                token_endpoint=token_endpoint,
                catalog_api_id=payload.catalog_api_id,
                display_name=payload.display_name,
                default_scopes=payload.default_scopes,
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
                "name": payload.name,
                "api_vendor": payload.api_vendor,
                "catalog_api_id": payload.catalog_api_id,
                "display_name": payload.display_name,
                "flow_kind": FLOW_KIND_DEVICE_AUTHORIZATION,
                "client_id": payload.client_id,
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

    async def list_page(self, query: OAuthAppRegistrationQuery) -> Page[OAuthAppRegistrationView]:
        """List registrations with cursor-based pagination."""
        decoded_cursor = (
            decode_cursor(query.cursor, id_kind="str") if query.cursor is not None else None
        )
        async with self._ctx.control_db.session() as session:
            rows = await OAuthAppRegistrationRepository.list_page(
                session,
                api_vendor=query.api_vendor,
                flow_kind=query.flow_kind,
                include_inactive=query.include_inactive,
                cursor=decoded_cursor,
                limit=query.limit + 1,
            )
            has_more = len(rows) > query.limit
            if has_more:
                rows = rows[: query.limit]
            counts = await OAuthAppRegistrationRepository.dependent_credential_counts(
                session, [row.id for row in rows]
            )
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor(last.created_at, last.id)
        views = [_project(row, dependent_credential_count=counts.get(row.id, 0)) for row in rows]
        return Page(data=views, has_more=has_more, next_cursor=next_cursor)

    async def update(
        self,
        registration_id: str,
        payload: OAuthAppRegistrationUpdate,
        *,
        identity: Identity,
    ) -> OAuthAppRegistrationView:
        egress = resolved_control_egress(self._ctx.config)
        authorize_url = await _validate_optional_endpoint_url(
            "authorize_url", payload.authorize_url, egress
        )
        token_url = await _validate_optional_endpoint_url("token_url", payload.token_url, egress)
        authorization_endpoint = await _validate_optional_endpoint_url(
            "authorization_endpoint", payload.authorization_endpoint, egress
        )
        token_endpoint = await _validate_optional_endpoint_url(
            "token_endpoint", payload.token_endpoint, egress
        )
        async with self._ctx.control_db.transaction() as session:
            registration = await OAuthAppRegistrationRepository.get_by_id(session, registration_id)
            if registration is None:
                raise OAuthAppRegistrationNotFoundError(registration_id)

            before = _snapshot(registration)

            if (
                payload.name is not None
                or payload.display_name is not None
                or payload.is_active is not None
            ):
                await OAuthAppRegistrationRepository.update_base(
                    session,
                    registration_id,
                    name=payload.name,
                    display_name=payload.display_name,
                    is_active=payload.is_active,
                )

            if registration.flow_kind == FLOW_KIND_AUTH_CODE:
                if authorization_endpoint is not None or token_endpoint is not None:
                    raise InvalidOAuthAppRegistrationInputError(
                        "authorization_endpoint / token_endpoint are only valid on "
                        "device-authorization registrations"
                    )
                ac = registration.authorization_code_details
                if (
                    token_url is not None
                    and ac is not None
                    and not same_origin(token_url, ac.token_url)
                ):
                    raise InvalidOAuthAppRegistrationInputError(
                        "authorization-code registrations cannot move token_url to a different "
                        "origin (scheme, host or port); create a new registration instead"
                    )
                if any(v is not None for v in (authorize_url, token_url, payload.default_scopes)):
                    await OAuthAppRegistrationRepository.update_authorization_code_details(
                        session,
                        registration_id,
                        authorize_url=authorize_url,
                        token_url=token_url,
                        default_scopes=payload.default_scopes,
                    )
            else:
                if authorize_url is not None or token_url is not None:
                    raise InvalidOAuthAppRegistrationInputError(
                        "authorize_url / token_url are only valid on authorization-code "
                        "registrations"
                    )
                dev = registration.device_authorization_details
                if (
                    token_endpoint is not None
                    and dev is not None
                    and not same_origin(token_endpoint, dev.token_endpoint)
                ):
                    raise InvalidOAuthAppRegistrationInputError(
                        "device-authorization registrations cannot move token_endpoint to a "
                        "different origin (scheme, host or port); create a new registration "
                        "instead"
                    )
                if (
                    authorization_endpoint is not None
                    and dev is not None
                    and not same_origin(authorization_endpoint, dev.authorization_endpoint)
                ):
                    raise InvalidOAuthAppRegistrationInputError(
                        "device-authorization registrations cannot move authorization_endpoint "
                        "to a different origin (scheme, host or port); create a new registration "
                        "instead"
                    )
                if any(
                    v is not None
                    for v in (authorization_endpoint, token_endpoint, payload.default_scopes)
                ):
                    await OAuthAppRegistrationRepository.update_device_authorization_details(
                        session,
                        registration_id,
                        authorization_endpoint=authorization_endpoint,
                        token_endpoint=token_endpoint,
                        default_scopes=payload.default_scopes,
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


async def _validate_endpoint_url(field: str, value: str, egress: EgressConfig) -> str:
    """Require an https URL that passes the upstream SSRF guard.

    These endpoints receive client credentials and authorization codes, so
    plain http and private/metadata targets the control-plane egress policy
    does not allowlist are refused at write time.
    """
    if not value.strip().lower().startswith("https://"):
        raise InvalidOAuthAppRegistrationInputError(f"Invalid {field}: must use https")
    try:
        return await validate_upstream_url_async(value, egress)
    except ValueError as exc:
        raise InvalidOAuthAppRegistrationInputError(f"Invalid {field}: {exc}") from exc


async def _validate_optional_endpoint_url(
    field: str, value: str | None, egress: EgressConfig
) -> str | None:
    return None if value is None else await _validate_endpoint_url(field, value, egress)


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
