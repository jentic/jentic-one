"""Credentials router — CRUD + connect flow for credential management."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse, RedirectResponse

from jentic_one.control.services.credentials.connect_service import (
    ConnectFlowError,
    ConnectService,
)
from jentic_one.control.services.credentials.errors import CredentialNotFoundError
from jentic_one.control.services.credentials.providers.base import (
    NotConnectableError,
    ProviderError,
)
from jentic_one.control.services.credentials.schemas.connect import (
    ConnectCallback,
    ConnectRequest,
)
from jentic_one.control.services.credentials.schemas.credentials import (
    CredentialCreate,
    CredentialRedactedView,
    CredentialUpdate,
)
from jentic_one.control.services.credentials.schemas.provision import APIReference
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.control.web.deps import (
    get_connect_service,
    get_credential_service,
)
from jentic_one.control.web.schemas.credentials import (
    APIReferenceResponse,
    ConnectChallengeResponse,
    ConnectRequestBody,
    CredentialCreateRequest,
    CredentialCreateResponse,
    CredentialListResponse,
    CredentialRedactedResponse,
    CredentialUpdateRequest,
    ProviderDiscoveryEntryResponse,
    ProviderDiscoveryResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models.credentials import CredentialType
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web.openapi_responses import conflict, not_found, with_responses
from jentic_one.shared.web.static import SPA_MOUNT_PATH

router = APIRouter()

_logger = structlog.get_logger(__name__)


def _to_redacted_response(view: CredentialRedactedView) -> CredentialRedactedResponse:
    """Project a service CredentialRedactedView to a web response."""
    api = view.api
    details = view.details
    return CredentialRedactedResponse(
        credential_id=view.credential_id,
        type=view.type,
        name=view.name,
        api=APIReferenceResponse(vendor=api.vendor, name=api.name, version=api.version),
        provider=view.provider,
        provider_account_ref=view.provider_account_ref,
        active=view.active,
        created_by=view.created_by,
        created_at=view.created_at,
        updated_at=view.updated_at,
        details=details.model_dump(exclude_none=True) if details else None,
        server_variables=view.server_variables,
    )


# OR-lists ``owner:credentials:read`` for parity with the other credential reads
# (list / get): a delegated agent is minted the owner scope, not the bare one, so
# gating providers on ``credentials:read`` alone would 403 an agent that can
# already read its owner's credentials. Provider discovery returns static config
# metadata (no ``build_access_filters``, nothing owner-scoped), so admitting the
# delegated agent here leaks nothing — it just keeps the credential reads uniform.
@router.get("/credentials/providers", summary="List credential providers")
async def list_providers(
    identity: Identity = get_current_identity(
        required_permissions=["credentials:read", "owner:credentials:read"]
    ),
    svc: CredentialService = Depends(get_credential_service),
) -> ProviderDiscoveryResponse:
    """Return discovery metadata for all configured credential providers."""
    entries = svc.list_providers()
    return ProviderDiscoveryResponse(
        providers=[
            ProviderDiscoveryEntryResponse(
                id=e.id,
                label=e.label,
                managed=e.managed,
                types=e.types,
                configured=e.configured,
                callback_url=e.callback_url,
            )
            for e in entries
        ]
    )


@router.post("/credentials", status_code=201, summary="Create credential")
async def create_credential(
    body: CredentialCreateRequest,
    identity: Identity = get_current_identity(required_permissions=["credentials:write"]),
    svc: CredentialService = Depends(get_credential_service),
) -> CredentialCreateResponse:
    """Create a new credential. The secret is returned once and never readable again."""
    payload = CredentialCreate(
        type=CredentialType(body.type),
        name=body.name,
        api=APIReference(
            vendor=body.api.vendor,
            name=body.api.name,
            version=body.api.version,
        ),
        provider=body.provider,
        server_variables=body.server_variables,
        token=getattr(body, "token", None),
        key=getattr(body, "key", None),
        location=getattr(body, "location", None),
        field_name=getattr(body, "field_name", None),
        username=getattr(body, "username", None),
        password=getattr(body, "password", None),
        grant_type=getattr(body, "grant_type", None),
        token_url=getattr(body, "token_url", None),
        authorize_url=getattr(body, "authorize_url", None),
        client_id=getattr(body, "client_id", None),
        client_secret=getattr(body, "client_secret", None),
        scopes=getattr(body, "scopes", None),
    )
    result = await svc.create(payload, identity=identity)

    redacted_api = APIReferenceResponse(
        vendor=result.api.vendor, name=result.api.name, version=result.api.version
    )
    redacted = CredentialRedactedResponse(
        credential_id=result.credential_id,
        type=result.type,
        name=result.name,
        api=redacted_api,
        provider=result.provider,
        active=result.active,
        created_at=result.created_at,
        server_variables=result.server_variables,
    )
    return CredentialCreateResponse(
        credential=redacted,
        secret=result.secret.model_dump(),
    )


@router.get("/credentials", summary="List credentials")
async def list_credentials(
    identity: Identity = get_current_identity(
        required_permissions=["credentials:read", "owner:credentials:read"]
    ),
    svc: CredentialService = Depends(get_credential_service),
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    vendor: str | None = Query(default=None),
) -> CredentialListResponse:
    """List credentials with cursor-based pagination."""
    page = await svc.list_all(cursor=cursor, limit=limit, vendor=vendor, identity=identity)
    return CredentialListResponse(
        data=[_to_redacted_response(v) for v in page.data],
        has_more=page.has_more,
        next_cursor=page.next_cursor,
    )


# The OAuth popup the SPA opened lands back here after the IdP round-trip.
# Rather than render product UI from the API (the backend has no business
# emitting HTML, and any inline copy would be served as the API's content
# type), we 302-redirect the popup to a tiny public SPA route that owns the
# user-facing "you can close this" experience and runs window.close().
#
# Only a coarse status travels in the query string — never a reason. The
# parent SPA learns the real outcome by polling GET /credentials/{id}; the
# actual cause (missing state, connect failure, provider error) is recorded
# via structured logging for operators. ``status=error`` carries no detail
# so no PII / IDs / provider-internal text can leak through the redirect URL
# (which is visible in browser history, referrer headers, and server logs).
#
# Served same-origin in every deploy mode (the SPA bundle is mounted under
# ``SPA_MOUNT_PATH``, see shared/web/static.py), so a root-absolute path reaches
# the SPA without knowing the host. The ``/app`` prefix is owned by
# ``SPA_MOUNT_PATH`` (the backend's single source, mirroring the UI's router
# basename) — derived here, never hand-written, so the two can't drift. Kept in
# lockstep with the public route registered in ui/src/App.tsx (which, under
# React Router ``basename="/app"``, declares it as ``/oauth/connected`` relative
# to the basename = ``/app/oauth/connected``).
_CONNECT_RETURN_PATH = f"{SPA_MOUNT_PATH}/oauth/connected"


def _oauth_callback_success() -> RedirectResponse:
    # 303 See Other: force the popup to GET the SPA return route regardless of
    # how it arrived, and avoid any body on the API origin.
    return RedirectResponse(f"{_CONNECT_RETURN_PATH}?status=ok", status_code=303)


def _oauth_callback_error() -> RedirectResponse:
    return RedirectResponse(f"{_CONNECT_RETURN_PATH}?status=error", status_code=303)


@router.get("/credentials/oauth/callback", summary="OAuth connect callback")
async def oauth_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    svc: ConnectService = Depends(get_connect_service),
) -> Response:
    """Handle the OAuth callback from the IdP.

    This endpoint is intentionally unauthenticated — it receives redirects
    from external IdPs where the user has no session cookie. Security
    binding is provided by the signed, time-limited state JWT which ties
    the callback to a specific credential and caller.

    Redirects the popup the SPA opened to a public SPA route
    (``/app/oauth/connected``) that owns the user-facing "you can close this"
    experience and self-closes. Two variants, distinguished only by a coarse
    ``status`` query param:

      * Success: ``?status=ok``.
      * Failure: ``?status=error`` — no protocol or provider detail is
        exposed in the redirect URL.

    The parent SPA still learns the real outcome by polling
    ``GET /credentials/{id}`` — never from this redirect. The actual cause
    (missing state, connect failure, provider error, etc.) is recorded via
    structured logging for operators.
    """
    if not state:
        # Almost certainly someone hand-typed/probed the URL (the IdP always
        # echoes state). Don't go through the service — there's nothing to
        # complete.
        _logger.warning(
            "oauth_callback.missing_state",
            has_code=bool(code),
            has_error=bool(error),
            error=error,
        )
        return _oauth_callback_error()

    callback = ConnectCallback(code=code, error=error)

    try:
        credential_id = await svc.complete(state, callback)
    except ConnectFlowError as exc:
        _logger.warning(
            "oauth_callback.connect_flow_error",
            error=str(exc),
            callback_error=error,
        )
        return _oauth_callback_error()
    except CredentialNotFoundError:
        _logger.warning("oauth_callback.credential_not_found")
        return _oauth_callback_error()
    except ProviderError as exc:
        _logger.warning("oauth_callback.provider_error", error=str(exc))
        return _oauth_callback_error()

    _logger.info("oauth_callback.connected", credential_id=credential_id)
    return _oauth_callback_success()


@router.get("/credentials/{credential_id}", summary="Get credential", responses=not_found())
async def get_credential(
    credential_id: str,
    identity: Identity = get_current_identity(
        required_permissions=["credentials:read", "owner:credentials:read"]
    ),
    svc: CredentialService = Depends(get_credential_service),
) -> CredentialRedactedResponse:
    """Get a single credential with redacted secrets."""
    view = await svc.get(credential_id, identity=identity)
    return _to_redacted_response(view)


@router.patch(
    "/credentials/{credential_id}",
    summary="Update or rotate credential",
    responses=not_found(),
)
async def update_credential(
    credential_id: str,
    body: CredentialUpdateRequest,
    identity: Identity = get_current_identity(required_permissions=["credentials:write"]),
    svc: CredentialService = Depends(get_credential_service),
) -> CredentialRedactedResponse:
    """Update or rotate a credential."""
    payload = CredentialUpdate(
        type=CredentialType(body.type),
        name=body.name,
        active=body.active,
        server_variables=body.server_variables,
        token=getattr(body, "token", None),
        key=getattr(body, "key", None),
        location=getattr(body, "location", None),
        field_name=getattr(body, "field_name", None),
        username=getattr(body, "username", None),
        password=getattr(body, "password", None),
        client_secret=getattr(body, "client_secret", None),
        token_url=getattr(body, "token_url", None),
        scopes=getattr(body, "scopes", None),
    )
    view = await svc.update(credential_id, payload, identity=identity)
    return _to_redacted_response(view)


@router.delete(
    "/credentials/{credential_id}",
    status_code=204,
    summary="Delete credential",
    responses=not_found(),
)
async def delete_credential(
    credential_id: str,
    identity: Identity = get_current_identity(required_permissions=["credentials:write"]),
    svc: CredentialService = Depends(get_credential_service),
) -> Response:
    """Delete a credential."""
    await svc.delete(credential_id, identity=identity)
    return Response(status_code=204)


@router.post(
    "/credentials/{credential_id}/connect",
    summary="Begin OAuth connect flow",
    responses=with_responses(not_found(), conflict("Credential is not connectable")),
)
async def connect_credential(
    credential_id: str,
    body: ConnectRequestBody,
    identity: Identity = get_current_identity(required_permissions=["credentials:write"]),
    svc: ConnectService = Depends(get_connect_service),
) -> ConnectChallengeResponse:
    """Initiate the OAuth connect flow for a credential."""
    connect_req = ConnectRequest(scopes=body.scopes, extra=body.extra)
    try:
        challenge = await svc.begin(
            credential_id,
            connect_req,
            actor_id=identity.sub,
            actor_type=identity.actor_type,
        )
    except CredentialNotFoundError:
        return JSONResponse(status_code=404, content={"detail": "Credential not found"})  # type: ignore[return-value]
    except NotConnectableError as exc:
        return JSONResponse(status_code=409, content={"detail": str(exc)})  # type: ignore[return-value]
    except ProviderError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})  # type: ignore[return-value]
    return ConnectChallengeResponse(authorize_url=challenge.authorize_url, state=challenge.state)
