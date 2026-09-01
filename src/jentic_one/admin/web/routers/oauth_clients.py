"""OAuth clients router — manage registered third-party OAuth applications."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response

from jentic_one.admin.services.oauth_client_service import OAuthClientService
from jentic_one.admin.services.schemas.oauth_clients import OAuthClientView
from jentic_one.admin.web.deps import get_oauth_client_service
from jentic_one.admin.web.schemas.oauth_clients import (
    OAuthClientCreateRequest,
    OAuthClientCreateResponse,
    OAuthClientDenyRequest,
    OAuthClientListResponse,
    OAuthClientResponse,
    OAuthClientRotateSecretResponse,
    OAuthClientUpdateRequest,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web.openapi_responses import not_found

router = APIRouter()


def _to_response(view: OAuthClientView) -> OAuthClientResponse:
    return OAuthClientResponse(
        id=view.id,
        client_id=view.client_id,
        name=view.name,
        description=view.description,
        redirect_uris=view.redirect_uris,
        allowed_scopes=view.allowed_scopes,
        active=view.active,
        require_consent=view.require_consent,
        token_endpoint_auth_method=view.token_endpoint_auth_method,
        consent_model=view.consent_model,
        registration_source=view.registration_source,
        software_id=view.software_id,
        approval_status=view.approval_status,
        created_at=view.created_at,
        updated_at=view.updated_at,
        created_by=view.created_by,
    )


@router.post("/admin/oauth-clients", status_code=201, summary="Register OAuth client")
async def create_oauth_client(
    body: OAuthClientCreateRequest,
    identity: Identity = get_current_identity(required_permissions=["oauth-clients:write"]),
    svc: OAuthClientService = Depends(get_oauth_client_service),
) -> OAuthClientCreateResponse:
    """Register a new OAuth client for third-party application integration.

    The generated ``client_id`` and ``client_secret`` are returned in the
    response. The secret is shown only once — store it securely.
    """
    result = await svc.create(
        name=body.name,
        description=body.description,
        redirect_uris=body.redirect_uris,
        require_consent=body.require_consent,
        allowed_scopes=body.allowed_scopes,
        token_endpoint_auth_method=body.token_endpoint_auth_method,
        consent_model=body.consent_model,
        identity=identity,
    )
    base = _to_response(result)
    return OAuthClientCreateResponse(**base.model_dump(), client_secret=result.client_secret)


@router.get("/admin/oauth-clients", summary="List OAuth clients")
async def list_oauth_clients(
    identity: Identity = get_current_identity(required_permissions=["oauth-clients:read"]),
    svc: OAuthClientService = Depends(get_oauth_client_service),
    include_inactive: bool = False,
    approval_status: str | None = Query(
        default=None,
        description=(
            "Filter by approval lifecycle state: pending, approved, or denied. "
            "Filtering on pending or denied implies include_inactive=true — "
            "those rows are always inactive until approved."
        ),
    ),
) -> OAuthClientListResponse:
    """List all registered OAuth clients, optionally filtered by approval status."""
    views = await svc.list_all(include_inactive=include_inactive, approval_status=approval_status)
    return OAuthClientListResponse(data=[_to_response(v) for v in views])


@router.get(
    "/admin/oauth-clients/{id}",
    summary="Get OAuth client",
    responses=not_found(),
)
async def get_oauth_client(
    id: str,
    identity: Identity = get_current_identity(required_permissions=["oauth-clients:read"]),
    svc: OAuthClientService = Depends(get_oauth_client_service),
) -> OAuthClientResponse:
    """Get an OAuth client by its internal ID."""
    view = await svc.get(id)
    return _to_response(view)


@router.patch(
    "/admin/oauth-clients/{id}",
    summary="Update OAuth client",
    responses=not_found(),
)
async def update_oauth_client(
    id: str,
    body: OAuthClientUpdateRequest,
    identity: Identity = get_current_identity(required_permissions=["oauth-clients:write"]),
    svc: OAuthClientService = Depends(get_oauth_client_service),
) -> OAuthClientResponse:
    """Update an OAuth client's name, description, redirect_uris, or active status."""
    view = await svc.update(
        id,
        name=body.name,
        description=body.description,
        redirect_uris=body.redirect_uris,
        active=body.active,
        require_consent=body.require_consent,
        allowed_scopes=body.allowed_scopes,
        identity=identity,
    )
    return _to_response(view)


@router.post(
    "/admin/oauth-clients/{id}:approve",
    summary="Approve OAuth client",
    responses=not_found(),
)
async def approve_oauth_client(
    id: str,
    identity: Identity = get_current_identity(required_permissions=["oauth-clients:write"]),
    svc: OAuthClientService = Depends(get_oauth_client_service),
) -> OAuthClientResponse:
    """Approve an OAuth client — sets approval_status=approved and active=true.

    Also re-approves a previously denied client (deny is reversible; rows are
    never deleted, so the client's cached client_id becomes valid again).
    """
    view = await svc.approve(id, identity=identity)
    return _to_response(view)


@router.post(
    "/admin/oauth-clients/{id}:deny",
    summary="Deny OAuth client",
    responses=not_found(),
)
async def deny_oauth_client(
    id: str,
    body: OAuthClientDenyRequest | None = None,
    identity: Identity = get_current_identity(required_permissions=["oauth-clients:write"]),
    svc: OAuthClientService = Depends(get_oauth_client_service),
) -> OAuthClientResponse:
    """Deny an OAuth client — sets approval_status=denied and active=false.

    The row is retained so a later approve can reverse the decision.
    """
    view = await svc.deny(id, reason=body.reason if body else None, identity=identity)
    return _to_response(view)


@router.post(
    "/admin/oauth-clients/{id}/rotate-secret",
    summary="Rotate client secret",
    responses=not_found(),
)
async def rotate_oauth_client_secret(
    id: str,
    identity: Identity = get_current_identity(required_permissions=["oauth-clients:write"]),
    svc: OAuthClientService = Depends(get_oauth_client_service),
) -> OAuthClientRotateSecretResponse:
    """Generate a new client secret. The previous secret is immediately invalidated.

    The new secret is shown only once — store it securely.
    """
    client_secret = await svc.rotate_secret(id, identity=identity)
    return OAuthClientRotateSecretResponse(client_secret=client_secret)


@router.delete(
    "/admin/oauth-clients/{id}",
    status_code=204,
    summary="Deactivate OAuth client",
    responses=not_found(),
)
async def deactivate_oauth_client(
    id: str,
    identity: Identity = get_current_identity(required_permissions=["oauth-clients:write"]),
    svc: OAuthClientService = Depends(get_oauth_client_service),
) -> Response:
    """Soft-delete an OAuth client by setting active=False.

    Deactivated clients can no longer initiate authorization flows.
    """
    await svc.deactivate(id, identity=identity)
    return Response(status_code=204)
