"""Service accounts router — lifecycle CRUD."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from jentic.problem_details import Forbidden

from jentic_one.auth.services.schemas.service_accounts import (
    ServiceAccountCreatePayload,
    ServiceAccountView,
)
from jentic_one.auth.services.service_account_auth_service import ServiceAccountAuthService
from jentic_one.auth.services.service_account_service import ServiceAccountService
from jentic_one.auth.web.deps import get_service_account_auth_service, get_service_account_service
from jentic_one.auth.web.schemas.agents import ApiKeyResponse
from jentic_one.auth.web.schemas.service_accounts import (
    DenyRequest,
    ServiceAccountCreateRequest,
    ServiceAccountListResponse,
    ServiceAccountResponse,
    ServiceAccountScopesRequest,
    ServiceAccountScopesResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.web import get_current_identity

router = APIRouter()


def _sa_response(view: ServiceAccountView) -> ServiceAccountResponse:
    return ServiceAccountResponse(
        id=view.id,
        name=view.name,
        description=view.description,
        owner_id=view.owner_id,
        registered_by=view.registered_by,
        approved_by=view.approved_by,
        status=view.status,
        denial_reason=view.denial_reason,
        denied_by=view.denied_by,
        created_at=view.created_at,
        approved_at=view.approved_at,
    )


def _is_admin(identity: Identity) -> bool:
    return "org:admin" in identity.permissions


@router.post("/service-accounts", status_code=201)
async def create_service_account(
    body: ServiceAccountCreateRequest,
    identity: Identity = get_current_identity(required_permissions=["service-accounts:write"]),
    sa_svc: ServiceAccountService = Depends(get_service_account_service),
) -> ServiceAccountResponse:
    """Create a new service account."""
    payload = ServiceAccountCreatePayload(
        name=body.name, description=body.description, scopes=body.scopes
    )
    view = await sa_svc.create(
        payload,
        owner_id=identity.sub,
        identity=identity,
    )
    return _sa_response(view)


@router.get("/service-accounts")
async def list_service_accounts(
    identity: Identity = get_current_identity(required_permissions=["service-accounts:read"]),
    sa_svc: ServiceAccountService = Depends(get_service_account_service),
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    status: str | None = Query(default=None),
) -> ServiceAccountListResponse:
    """List service accounts — owner-scoped unless caller is org:admin."""
    if _is_admin(identity):
        page = await sa_svc.list_service_accounts(
            limit=limit, status=status, cursor=cursor, identity=identity
        )
    else:
        page = await sa_svc.list_service_accounts(
            owner_id=identity.sub, limit=limit, cursor=cursor, identity=identity
        )
    return ServiceAccountListResponse(
        data=[_sa_response(a) for a in page.data],
        has_more=page.has_more,
        next_cursor=page.next_cursor,
    )


@router.get("/service-accounts/{service_account_id}")
async def get_service_account(
    service_account_id: str,
    request: Request,
    identity: Identity = get_current_identity(allow_expired_password=True),
    sa_svc: ServiceAccountService = Depends(get_service_account_service),
) -> ServiceAccountResponse:
    """Get service account by ID — requires service-accounts:read or self-read."""
    view = await sa_svc.get_service_account(service_account_id, identity=identity)
    _check_read_access(identity, view, request)
    return _sa_response(view)


@router.post("/service-accounts/{service_account_id}:approve", status_code=200)
async def approve_service_account(
    service_account_id: str,
    identity: Identity = get_current_identity(required_permissions=["service-accounts:write"]),
    sa_svc: ServiceAccountService = Depends(get_service_account_service),
) -> ServiceAccountResponse:
    """Approve a pending service account."""
    view = await sa_svc.approve(service_account_id, identity=identity)
    return _sa_response(view)


@router.post("/service-accounts/{service_account_id}:deny", status_code=200)
async def deny_service_account(
    service_account_id: str,
    body: DenyRequest,
    identity: Identity = get_current_identity(required_permissions=["service-accounts:write"]),
    sa_svc: ServiceAccountService = Depends(get_service_account_service),
) -> ServiceAccountResponse:
    """Deny a pending service account."""
    view = await sa_svc.deny(service_account_id, reason=body.reason, identity=identity)
    return _sa_response(view)


@router.post("/service-accounts/{service_account_id}:disable", status_code=204)
async def disable_service_account(
    service_account_id: str,
    identity: Identity = get_current_identity(required_permissions=["service-accounts:write"]),
    sa_svc: ServiceAccountService = Depends(get_service_account_service),
) -> Response:
    """Disable an active service account."""
    await sa_svc.disable(service_account_id, identity=identity)
    return Response(status_code=204)


@router.post("/service-accounts/{service_account_id}:enable", status_code=204)
async def enable_service_account(
    service_account_id: str,
    identity: Identity = get_current_identity(required_permissions=["service-accounts:write"]),
    sa_svc: ServiceAccountService = Depends(get_service_account_service),
) -> Response:
    """Enable a disabled service account."""
    await sa_svc.enable(service_account_id, identity=identity)
    return Response(status_code=204)


@router.delete("/service-accounts/{service_account_id}", status_code=204)
async def archive_service_account(
    service_account_id: str,
    identity: Identity = get_current_identity(required_permissions=["service-accounts:write"]),
    sa_svc: ServiceAccountService = Depends(get_service_account_service),
) -> Response:
    """Archive a service account — terminal-but-kept.

    The row is retained for history, but the action is not reversible and
    the account's scope grants are revoked. For the reversible kill switch
    use ``:disable`` / ``:enable`` instead.
    """
    await sa_svc.archive(service_account_id, identity=identity)
    return Response(status_code=204)


@router.get("/service-accounts/{service_account_id}/scopes", operation_id="getServiceAccountScopes")
async def get_service_account_scopes(
    service_account_id: str,
    identity: Identity = get_current_identity(required_permissions=["service-accounts:read"]),
    sa_svc: ServiceAccountService = Depends(get_service_account_service),
) -> ServiceAccountScopesResponse:
    """List scopes granted to a service account."""
    scopes = await sa_svc.get_scopes(service_account_id, identity=identity)
    return ServiceAccountScopesResponse(scopes=scopes)


@router.put(
    "/service-accounts/{service_account_id}/scopes",
    operation_id="replaceServiceAccountScopes",
)
async def replace_service_account_scopes(
    service_account_id: str,
    body: ServiceAccountScopesRequest,
    identity: Identity = get_current_identity(required_permissions=["service-accounts:write"]),
    sa_svc: ServiceAccountService = Depends(get_service_account_service),
) -> ServiceAccountScopesResponse:
    """Replace all scopes for a service account."""
    scopes = await sa_svc.replace_scopes(service_account_id, body.scopes, identity=identity)
    return ServiceAccountScopesResponse(scopes=scopes)


@router.post("/service-accounts/{service_account_id}:generate-api-key", status_code=200)
async def generate_service_account_api_key(
    service_account_id: str,
    identity: Identity = get_current_identity(required_permissions=["service-accounts:write"]),
    auth_svc: ServiceAccountAuthService = Depends(get_service_account_auth_service),
) -> ApiKeyResponse:
    """Generate a new API key for a service account. Rotates any existing key."""
    key = await auth_svc.register_api_key(service_account_id, identity=identity)
    return ApiKeyResponse(key=key)


def _check_read_access(identity: Identity, view: ServiceAccountView, request: Request) -> None:
    """Allow if caller has service-accounts:read, is org:admin, or is the SA itself."""
    caller_perms = set(identity.permissions)
    if "org:admin" in caller_perms or "service-accounts:read" in caller_perms:
        return
    if identity.sub == view.id or identity.sub == view.owner_id:
        return
    raise Forbidden(
        detail="You do not have access to this service account",
        instance=request.url.path,
        type="forbidden",
    )
