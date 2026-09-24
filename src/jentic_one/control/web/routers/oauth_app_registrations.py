"""OAuth app registrations router — admin CRUD for shared OAuth apps."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response

from jentic_one.control.services.oauth_app_registrations.schemas import (
    OAuthAppRegistrationFlowKind,
    OAuthAppRegistrationView,
)
from jentic_one.control.services.oauth_app_registrations.service import (
    OAuthAppRegistrationService,
)
from jentic_one.control.web.deps import get_oauth_app_registration_service
from jentic_one.control.web.schemas.oauth_app_registrations import (
    AuthorizationCodeRegistrationCreateRequest,
    DeviceAuthorizationRegistrationCreateRequest,
    OAuthAppRegistrationCreateRequest,
    OAuthAppRegistrationListResponse,
    OAuthAppRegistrationResponse,
    OAuthAppRegistrationRotateSecretRequest,
    OAuthAppRegistrationUpdateRequest,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import ORG_ADMIN
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web.openapi_responses import conflict, not_found, with_responses

router = APIRouter()


def _to_response(view: OAuthAppRegistrationView) -> OAuthAppRegistrationResponse:
    return OAuthAppRegistrationResponse(
        id=view.id,
        name=view.name,
        api_vendor=view.api_vendor,
        flow_kind=view.flow_kind,
        client_id=view.client_id,
        is_active=view.is_active,
        has_client_secret=view.has_client_secret,
        secret_last_rotated_at=view.secret_last_rotated_at,
        authorize_url=view.authorize_url,
        token_url=view.token_url,
        authorization_endpoint=view.authorization_endpoint,
        token_endpoint=view.token_endpoint,
        default_scopes=view.default_scopes,
        created_at=view.created_at,
        updated_at=view.updated_at,
        created_by=view.created_by,
        dependent_credential_count=view.dependent_credential_count,
    )


@router.post(
    "/oauth-app-registrations",
    status_code=201,
    summary="Register a shared OAuth application",
)
async def create_oauth_app_registration(
    body: OAuthAppRegistrationCreateRequest,
    identity: Identity = get_current_identity(required_permissions=[ORG_ADMIN]),
    svc: OAuthAppRegistrationService = Depends(get_oauth_app_registration_service),
) -> OAuthAppRegistrationResponse:
    """Register a shared OAuth application that users on this instance can SSO through.

    The client secret is stored encrypted and never returned by any read
    endpoint — reads only expose ``has_client_secret`` + ``secret_last_rotated_at``.
    """
    if isinstance(body, AuthorizationCodeRegistrationCreateRequest):
        view = await svc.create_authorization_code(
            name=body.name,
            api_vendor=body.api_vendor,
            client_id=body.client_id,
            client_secret=body.client_secret,
            authorize_url=body.authorize_url,
            token_url=body.token_url,
            default_scopes=body.default_scopes,
            identity=identity,
        )
    else:
        assert isinstance(body, DeviceAuthorizationRegistrationCreateRequest)
        view = await svc.create_device_authorization(
            name=body.name,
            api_vendor=body.api_vendor,
            client_id=body.client_id,
            authorization_endpoint=body.authorization_endpoint,
            token_endpoint=body.token_endpoint,
            default_scopes=body.default_scopes,
            identity=identity,
        )
    return _to_response(view)


@router.get("/oauth-app-registrations", summary="List OAuth app registrations")
async def list_oauth_app_registrations(
    identity: Identity = get_current_identity(required_permissions=["credentials:read"]),
    svc: OAuthAppRegistrationService = Depends(get_oauth_app_registration_service),
    api_vendor: str | None = Query(default=None, description="Filter by vendor slug."),
    include_inactive: bool = Query(
        default=False,
        description="Include registrations with is_active=false in the response.",
    ),
    flow_kind: OAuthAppRegistrationFlowKind | None = Query(
        default=None, description="Filter by OAuth flow kind."
    ),
) -> OAuthAppRegistrationListResponse:
    """List OAuth application registrations visible to the caller.

    Any authenticated caller with ``credentials:read`` sees the shared
    registrations they might connect through; admin-only routes gate on
    ``org:admin`` separately.
    """
    views = await svc.list_all(api_vendor=api_vendor, include_inactive=include_inactive)
    if flow_kind is not None:
        views = [v for v in views if v.flow_kind == flow_kind]
    return OAuthAppRegistrationListResponse(data=[_to_response(v) for v in views])


@router.get(
    "/oauth-app-registrations/{id}",
    summary="Get an OAuth app registration",
    responses=not_found(),
)
async def get_oauth_app_registration(
    id: str,
    identity: Identity = get_current_identity(required_permissions=["credentials:read"]),
    svc: OAuthAppRegistrationService = Depends(get_oauth_app_registration_service),
) -> OAuthAppRegistrationResponse:
    """Get an OAuth app registration by id."""
    view = await svc.get(id)
    return _to_response(view)


@router.patch(
    "/oauth-app-registrations/{id}",
    summary="Update an OAuth app registration",
    responses=not_found(),
)
async def update_oauth_app_registration(
    id: str,
    body: OAuthAppRegistrationUpdateRequest,
    identity: Identity = get_current_identity(required_permissions=[ORG_ADMIN]),
    svc: OAuthAppRegistrationService = Depends(get_oauth_app_registration_service),
) -> OAuthAppRegistrationResponse:
    """Update mutable fields on a registration.

    ``client_id`` is immutable (would orphan every dependent credential) —
    use ``:rotate-secret`` for the client secret.
    """
    view = await svc.update(
        id,
        name=body.name,
        is_active=body.is_active,
        default_scopes=body.default_scopes,
        authorize_url=body.authorize_url,
        token_url=body.token_url,
        authorization_endpoint=body.authorization_endpoint,
        token_endpoint=body.token_endpoint,
        identity=identity,
    )
    return _to_response(view)


@router.post(
    "/oauth-app-registrations/{id}:rotate-secret",
    summary="Rotate the client secret",
    responses=not_found(),
)
async def rotate_oauth_app_registration_secret(
    id: str,
    body: OAuthAppRegistrationRotateSecretRequest,
    identity: Identity = get_current_identity(required_permissions=[ORG_ADMIN]),
    svc: OAuthAppRegistrationService = Depends(get_oauth_app_registration_service),
) -> OAuthAppRegistrationResponse:
    """Replace the encrypted client secret. Existing tokens are unaffected;
    subsequent refreshes present the new secret."""
    view = await svc.rotate_client_secret(id, client_secret=body.client_secret, identity=identity)
    return _to_response(view)


@router.delete(
    "/oauth-app-registrations/{id}",
    status_code=204,
    summary="Delete an OAuth app registration",
    responses=with_responses(not_found(), conflict()),
)
async def delete_oauth_app_registration(
    id: str,
    identity: Identity = get_current_identity(required_permissions=[ORG_ADMIN]),
    svc: OAuthAppRegistrationService = Depends(get_oauth_app_registration_service),
) -> Response:
    """Delete a registration.

    Refused if any credentials still reference it — revoke those first, or
    deactivate the registration with a PATCH ``is_active=false`` instead.
    """
    await svc.delete(id, identity=identity)
    return Response(status_code=204)
