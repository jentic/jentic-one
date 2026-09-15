"""Integration connect-session endpoints."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from jentic_one.control.services.integrations.connect_session_service import (
    ConnectSessionService,
    DeviceAuthorizationConfirmResult,
)
from jentic_one.control.services.integrations.errors import (
    ConfirmationForbiddenError,
    ConnectSessionServiceError,
    InvalidPollTokenError,
    InvalidStateTransitionError,
    NoOpForFlowError,
    ScopeValidationError,
    SessionNotFoundError,
)
from jentic_one.control.services.vendors.service import (
    UnknownVendorError,
    UnsupportedFlowError,
    VendorNotConfiguredError,
)
from jentic_one.control.web.deps import get_connect_session_service
from jentic_one.control.web.schemas.integrations import (
    ApiReferenceResponse,
    AuthCodeConfirmSessionResponse,
    ConfirmSessionRequest,
    ConfirmSessionResponse,
    DeviceAuthorizationConfirmSessionResponse,
    IntegrationsConnectRequest,
    IntegrationsConnectResponse,
    ReviewScopeResponse,
    ReviewSessionResponse,
    StatusResponse,
)
from jentic_one.control.web.schemas.permission_rules import PermissionRuleSchema
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorType
from jentic_one.shared.resilience import RateLimiter
from jentic_one.shared.state import MemoryStateBackend
from jentic_one.shared.web import get_current_identity

_logger = structlog.get_logger(__name__)

router = APIRouter(tags=["Integrations"])


# ---------------------------------------------------------------------------
# Per-actor rate limit for POST /integrations:connect
# ---------------------------------------------------------------------------
#
# Every :connect POST fires the vendor's device-authorization / authorization
# endpoint. A ``credentials:write`` caller that spams the endpoint can get the
# platform IP throttled by GitHub / Google / etc., which would fail-fast every
# legitimate user on the same install. This is a per-actor cap that fires
# well below any vendor's own throttle: ``_CONNECT_RPM`` sustained requests
# per minute, ``_CONNECT_BURST`` bucket capacity for a short spike.
#
# In-memory only (per-worker) — sufficient for the abuse case (one actor
# spamming), not a coordinated-cluster limit. If a multi-worker cluster-wide
# limit is ever needed, wire ``build_state_backend(ctx.config.<...>.backend)``
# instead of the memory backend below.
_CONNECT_RPM = 30
_CONNECT_BURST = 10


def _get_connect_limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter | None = getattr(request.app.state, "integrations_connect_limiter", None)
    if limiter is not None:
        return limiter
    limiter = RateLimiter(
        MemoryStateBackend(),
        default_rpm=_CONNECT_RPM,
        burst=_CONNECT_BURST,
        namespace="integrations_connect",
    )
    request.app.state.integrations_connect_limiter = limiter
    return limiter


# ---------------------------------------------------------------------------
# POST /integrations:connect — begin a connect session (agent or UI initiated)
# ---------------------------------------------------------------------------


@router.post(
    "/integrations:connect",
    status_code=201,
    summary="Start an integration connect session",
    response_model=None,
)
async def integrations_connect(
    body: IntegrationsConnectRequest,
    request: Request,
    identity: Identity = get_current_identity(
        required_permissions=["credentials:connect", "credentials:write"]
    ),
    svc: ConnectSessionService = Depends(get_connect_session_service),
) -> IntegrationsConnectResponse | JSONResponse:
    """Both entrypoints (agent + UI) use this endpoint.

    Agent callers: `agent_id` in the payload is refused (the caller *is*
    the agent — spoofing another agent's id is a permission-boundary
    violation). The caller's own identity is injected instead. UI / user
    callers: `agent_id` is optional — when named, confirm creates the
    direct agent-credential binding + permission rules; when omitted, the
    credential connects unbound and an agent can be bound later through
    the credentials API.
    """
    # Per-actor rate limit — every :connect POST fires the vendor's
    # authorize/device-authorization endpoint, so a spammy caller can get
    # the platform IP throttled by the vendor and take out every legit
    # user on the same install. See ``_get_connect_limiter`` for the
    # policy knobs.
    limiter = _get_connect_limiter(request)
    outcome = await limiter.acquire(identity.sub)
    if not outcome.allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": "rate limit exceeded"},
            headers={**outcome.headers(), "Retry-After": str(outcome.retry_after_s)},
        )

    if identity.actor_type == ActorType.AGENT:
        if body.agent_id is not None:
            return JSONResponse(
                status_code=403,
                content={
                    "detail": (
                        "agent callers must not pass agent_id — the caller's identity is used"
                    )
                },
            )
        agent_id: str | None = identity.sub
    else:
        agent_id = body.agent_id or None

    try:
        created = await svc.create_session(
            vendor_key=body.vendor,
            agent_id=agent_id,
            initiator_actor_id=identity.sub,
            requested_scopes=body.requested_scopes,
            requested_permission_rules=[
                r.model_dump(exclude_none=True) for r in body.requested_permission_rules
            ],
            preferred_flow=body.preferred_flow,
            reason=body.reason,
        )
    except UnknownVendorError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    except UnsupportedFlowError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    except VendorNotConfiguredError as exc:
        return JSONResponse(
            status_code=503,
            content={"detail": str(exc)},
        )
    except NoOpForFlowError as exc:
        return JSONResponse(
            status_code=400,
            content={"detail": str(exc)},
        )

    return IntegrationsConnectResponse(
        session_id=created.session_id,
        approval_url=created.approval_url,
        poll_token=created.poll_token,
        resolved_flow=created.resolved_flow,
    )


# ---------------------------------------------------------------------------
# GET /connect-sessions/{id} — review-page data
# ---------------------------------------------------------------------------


@router.get(
    "/connect-sessions/{session_id}",
    summary="Get review data for a connect session",
    response_model=None,
)
async def get_connect_session(
    session_id: str,
    identity: Identity = get_current_identity(required_permissions=["credentials:write"]),
    svc: ConnectSessionService = Depends(get_connect_session_service),
) -> ReviewSessionResponse | JSONResponse:
    """Data the review page needs: vendor display name, resolved flow, the
    scope catalog flagged with default/requested, current state, reason.
    """
    try:
        data = await svc.get_review_data(session_id)
    except SessionNotFoundError:
        return JSONResponse(status_code=404, content={"detail": "session not found"})
    return ReviewSessionResponse(
        session_id=data.session_id,
        state=data.state,
        vendor_key=data.vendor_key,
        vendor_display_name=data.vendor_display_name,
        resolved_flow=data.resolved_flow,
        reason=data.reason,
        requested_by_actor_id=data.requested_by_actor_id,
        scopes=[
            ReviewScopeResponse(
                name=s.name,
                classification=s.classification,  # type: ignore[arg-type]
                default=s.default,
                requested=s.requested,
                description=s.description,
            )
            for s in data.scopes
        ],
        # Re-validate through ``PermissionRuleSchema`` on the way out so the
        # response contract stays honest even if the stored JSON is ever
        # hand-edited or migrated from an older shape.
        requested_permission_rules=[
            PermissionRuleSchema.model_validate(r) for r in data.requested_permission_rules
        ],
        api_reference=ApiReferenceResponse(
            vendor=data.api_vendor,
            name=data.api_name,
            version=data.api_version,
        ),
    )


# ---------------------------------------------------------------------------
# POST /connect-sessions/{id}:confirm
# ---------------------------------------------------------------------------


@router.post(
    "/connect-sessions/{session_id}:confirm",
    summary="Confirm scopes + permissions and kick off the vendor flow",
    response_model=None,
)
async def confirm_connect_session(
    session_id: str,
    body: ConfirmSessionRequest,
    identity: Identity = get_current_identity(required_permissions=["credentials:write"]),
    svc: ConnectSessionService = Depends(get_connect_session_service),
) -> ConfirmSessionResponse | JSONResponse:
    """Called by the review page after the human confirms selections."""
    try:
        result = await svc.confirm(
            session_id,
            confirmed_scopes=body.confirmed_scopes,
            permission_rules=[r.model_dump() for r in body.permission_rules],
            caller_actor_id=identity.sub,
            caller_actor_type=str(identity.actor_type),
        )
    except SessionNotFoundError:
        return JSONResponse(status_code=404, content={"detail": "session not found"})
    except InvalidStateTransitionError as exc:
        return JSONResponse(status_code=409, content={"detail": str(exc)})
    except ConfirmationForbiddenError as exc:
        return JSONResponse(status_code=403, content={"detail": str(exc)})
    except ScopeValidationError as exc:
        return JSONResponse(
            status_code=400,
            content={"detail": str(exc), "unknown_scopes": exc.unknown},
        )
    except NoOpForFlowError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    except ConnectSessionServiceError as exc:
        _logger.exception("connect_session.confirm_failed", session_id=session_id)
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    if isinstance(result, DeviceAuthorizationConfirmResult):
        return DeviceAuthorizationConfirmSessionResponse(
            user_code=result.user_code,
            verification_uri=result.verification_uri,
            verification_uri_complete=result.verification_uri_complete,
            poll_interval_seconds=result.poll_interval_seconds,
        )
    return AuthCodeConfirmSessionResponse(authorize_url=result.authorize_url)


# ---------------------------------------------------------------------------
# GET /connect-sessions/{id}/status
# ---------------------------------------------------------------------------


@router.get(
    "/connect-sessions/{session_id}/status",
    summary="Poll a connect session's status",
    response_model=None,
)
async def poll_connect_session_status(
    session_id: str,
    poll_token: str = Query(..., description="Opaque poll capability"),
    # Auth is intentionally lightweight: caller must be authenticated with a
    # credential-touching permission, but the poll_token is the real capability
    # that scopes the response — an agent given the token by :connect can poll
    # without full session-read auth.
    identity: Identity = get_current_identity(
        required_permissions=["credentials:connect", "credentials:write"]
    ),
    svc: ConnectSessionService = Depends(get_connect_session_service),
) -> StatusResponse | JSONResponse:
    try:
        result = await svc.get_status(session_id, poll_token=poll_token)
    except InvalidPollTokenError:
        # ``get_status`` uniformly raises this for both "session missing"
        # and "poll_token mismatch" — see ``connect_session_service.get_status``.
        # The uniform 403 is what closes the session-id enumeration oracle;
        # a 404 branch here would silently reintroduce the split.
        return JSONResponse(status_code=403, content={"detail": "invalid poll_token"})

    return StatusResponse(
        status=result.status,  # type: ignore[arg-type]
        connected_as=result.connected_as,
        credential_id=result.credential_id,
        bound_scopes=result.bound_scopes,
        error_code=result.error_code,
    )


# ---------------------------------------------------------------------------
# POST /connect-sessions/{id}:cancel — user aborts before terminal
# ---------------------------------------------------------------------------


@router.post(
    "/connect-sessions/{session_id}:cancel",
    status_code=204,
    summary="Cancel an in-flight connect session",
    response_model=None,
)
async def cancel_connect_session(
    session_id: str,
    poll_token: str = Query(..., description="Opaque poll capability"),
    identity: Identity = get_current_identity(
        required_permissions=["credentials:connect", "credentials:write"]
    ),
    svc: ConnectSessionService = Depends(get_connect_session_service),
) -> Response | JSONResponse:
    """Terminate a still-active session at the user's request.

    Gated by the same ``poll_token`` capability as ``/status`` — the
    SPA already holds it, so we don't force the caller to bring a
    heavier scope than the poller endpoint they're already using.

    A still-existing but already-terminal session is a 204 no-op — a
    "Cancel" click racing the poll scanner doesn't error. A session
    that has already been cascade-deleted (unhappy-terminal path in
    ``_mark_terminal``) surfaces as 403, matching ``/status`` — the
    caller can't distinguish "gone" from "your poll_token is wrong",
    which is the enumeration-oracle guard. The SPA's cancel-on-unmount
    is fire-and-forget and ``.catch``es the 403, so this doesn't leak
    into the UX.
    """
    try:
        await svc.cancel_session(session_id, poll_token=poll_token)
    except InvalidPollTokenError:
        return JSONResponse(status_code=403, content={"detail": "invalid poll_token"})
    return Response(status_code=204)
