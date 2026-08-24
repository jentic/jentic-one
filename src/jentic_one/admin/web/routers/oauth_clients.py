"""OAuth clients router — manage registered third-party OAuth applications."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from jentic_one.admin.services.oauth_client_service import OAuthClientService
from jentic_one.admin.services.schemas.oauth_clients import OAuthClientView
from jentic_one.admin.web.deps import get_oauth_client_service
from jentic_one.admin.web.schemas.oauth_clients import (
    OAuthClientCreateRequest,
    OAuthClientListResponse,
    OAuthClientResponse,
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
        created_at=view.created_at,
        updated_at=view.updated_at,
        created_by=view.created_by,
    )


@router.post("/admin/oauth-clients", status_code=201, summary="Register OAuth client")
async def create_oauth_client(
    body: OAuthClientCreateRequest,
    identity: Identity = get_current_identity(required_permissions=["org:admin"]),
    svc: OAuthClientService = Depends(get_oauth_client_service),
) -> OAuthClientResponse:
    """Register a new OAuth client for third-party application integration.

    The generated ``client_id`` is returned in the response and should be
    configured in the third-party application. No client_secret is issued —
    clients must use PKCE (S256) for authorization.
    """
    view = await svc.create(
        name=body.name,
        description=body.description,
        redirect_uris=body.redirect_uris,
        require_consent=body.require_consent,
        allowed_scopes=body.allowed_scopes,
        identity=identity,
    )
    return _to_response(view)


@router.get("/admin/oauth-clients", summary="List OAuth clients")
async def list_oauth_clients(
    identity: Identity = get_current_identity(required_permissions=["org:admin"]),
    svc: OAuthClientService = Depends(get_oauth_client_service),
    include_inactive: bool = False,
) -> OAuthClientListResponse:
    """List all registered OAuth clients."""
    views = await svc.list_all(include_inactive=include_inactive)
    return OAuthClientListResponse(data=[_to_response(v) for v in views])


@router.get(
    "/admin/oauth-clients/{id}",
    summary="Get OAuth client",
    responses=not_found(),
)
async def get_oauth_client(
    id: str,
    identity: Identity = get_current_identity(required_permissions=["org:admin"]),
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
    identity: Identity = get_current_identity(required_permissions=["org:admin"]),
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


@router.delete(
    "/admin/oauth-clients/{id}",
    status_code=204,
    summary="Deactivate OAuth client",
    responses=not_found(),
)
async def deactivate_oauth_client(
    id: str,
    identity: Identity = get_current_identity(required_permissions=["org:admin"]),
    svc: OAuthClientService = Depends(get_oauth_client_service),
) -> Response:
    """Soft-delete an OAuth client by setting active=False.

    Deactivated clients can no longer initiate authorization flows.
    """
    await svc.deactivate(id, identity=identity)
    return Response(status_code=204)
