"""Integration connect-session endpoints."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from jentic_one.control.services.integrations.connect_session_service import (
    ConnectSessionService,
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
    ConfirmSessionRequest,
    ConfirmSessionResponse,
    IntegrationsConnectRequest,
    IntegrationsConnectResponse,
    ReviewScopeResponse,
    ReviewSessionResponse,
    StatusResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorType
from jentic_one.shared.web import get_current_identity

_logger = structlog.get_logger(__name__)

router = APIRouter(tags=["integrations"])


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
    identity: Identity = get_current_identity(
        required_permissions=["credentials:connect", "credentials:write"]
    ),
    svc: ConnectSessionService = Depends(get_connect_session_service),
) -> IntegrationsConnectResponse | JSONResponse:
    """Both entrypoints (agent + UI) use this endpoint.

    Agent callers: `agent_id` in the payload is ignored; the calling agent's
    own identity is used. UI/user callers: `agent_id` is required.
    """
    # Resolve which agent the credential will be bound to.
    if identity.actor_type == ActorType.AGENT:
        agent_id = identity.sub
    else:
        if not body.agent_id:
            return JSONResponse(
                status_code=400,
                content={"detail": "agent_id is required for non-agent callers"},
            )
        agent_id = body.agent_id

    try:
        created = await svc.create_session(
            vendor_key=body.vendor,
            agent_id=agent_id,
            initiator_actor_id=identity.sub,
            requested_scopes=body.requested_scopes,
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

    return ConfirmSessionResponse(
        user_code=result.user_code,
        verification_uri=result.verification_uri,
        verification_uri_complete=result.verification_uri_complete,
        poll_interval_seconds=result.poll_interval_seconds,
    )


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
        result = await svc.poll_status(session_id, poll_token=poll_token)
    except SessionNotFoundError:
        return JSONResponse(status_code=404, content={"detail": "session not found"})
    except InvalidPollTokenError:
        return JSONResponse(status_code=403, content={"detail": "invalid poll_token"})

    return StatusResponse(
        status=result.status,  # type: ignore[arg-type]
        connected_as=result.connected_as,
        credential_id=result.credential_id,
        bound_scopes=result.bound_scopes,
        error_code=result.error_code,
    )
