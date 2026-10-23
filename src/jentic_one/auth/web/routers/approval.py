"""Approval endpoints behind the approval-pending page.

A registered-but-unapproved client at /authorize renders a live
approval-pending page instead of a dead stop (see ``routers/authorize.py``).
The page polls GET /oauth/approval/status (anonymous, rate limited, keyed by
a signed state blob — never a bare client_id) and, when the browser holds an
admin SPA session, offers inline approve/deny via POST
/oauth/approval/decision (a thin wrapper over the admin OAuthClientService
approve/deny with the same ``oauth-clients:write`` gate). On approval the page
re-runs the ORIGINAL authorize request, which now proceeds normally to the
IdP redirect; on denial it closes the loop with a standard
``error=access_denied`` redirect when the redirect_uri is registered for the
client.
"""

from __future__ import annotations

from typing import Literal

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response

from jentic_one.admin.services.oauth_client_service import OAuthClientService
from jentic_one.admin.services.schemas.oauth_clients import OAuthClientView
from jentic_one.auth.services.errors import InvalidGrantError
from jentic_one.auth.web.consent_state import verify_approval_state
from jentic_one.auth.web.flow import (
    check_approval_status_rate_limit,
    client_gate_passes,
    get_cached_oauth_client,
)
from jentic_one.auth.web.schemas.authorize import (
    OAuthApprovalDecisionRequest,
    OAuthApprovalStatusResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models.oauth_clients import OAuthClientApprovalStatus
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web import openapi_examples as ex
from jentic_one.shared.web.deps import get_ctx
from jentic_one.shared.web.openapi_responses import problem

logger = structlog.get_logger(__name__)

router = APIRouter()


def _approval_tri_state(
    client: OAuthClientView | None,
) -> Literal["pending", "approved", "denied"]:
    """Collapse a client row into the poll tri-state.

    ``approved`` only when the full client gate passes (approved AND active), so a
    kill-switched client is never announced as ready. A missing row reads as
    ``pending`` — rows are never deleted in normal operation, and answering
    anything else would turn the endpoint into a deletion oracle.
    """
    if client is None:
        return "pending"
    if client.approval_status == OAuthClientApprovalStatus.DENIED.value:
        return "denied"
    if client_gate_passes(client):
        return "approved"
    return "pending"


@router.get(
    "/oauth/approval/status",
    dependencies=[Depends(check_approval_status_rate_limit)],
    summary="Poll client approval status (approval-pending page)",
    responses={
        400: problem("Malformed, tampered, or expired approval-state blob."),
    },
)
async def approval_status_endpoint(
    request: Request,
    response: Response,
    st: str = Query(
        ...,
        description="Signed approval-state blob minted by /authorize",
        openapi_examples=ex.example(ex.SIGNED_FLOW_STATE),
    ),
    ctx: Context = Depends(get_ctx),
) -> OAuthApprovalStatusResponse:
    """Minimal tri-state poll for the approval-pending page.

    Anonymous but keyed by the signed approval-state blob — never a bare
    client_id, so the endpoint cannot be used to enumerate registrations. Any
    verification failure (bad signature, wrong purpose, expired ``iat``,
    malformed blob) is a 400 ``invalid_grant``; the page reacts to a 400 by
    re-running /authorize, which mints a fresh blob. The response carries ONLY
    the tri-state — no names, redirect URIs, or metadata.
    """
    params = verify_approval_state(ctx, st)
    client = await get_cached_oauth_client(request, str(params.get("client_id") or ""), ctx)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return OAuthApprovalStatusResponse(status=_approval_tri_state(client))


@router.post(
    "/oauth/approval/decision",
    summary="Approve or deny a pending client inline (approval-pending page)",
    responses={
        400: problem("Malformed, tampered, or expired approval-state blob."),
    },
)
async def approval_decision_endpoint(
    request: Request,
    response: Response,
    body: OAuthApprovalDecisionRequest,
    identity: Identity = get_current_identity(required_permissions=["oauth-clients:write"]),
    ctx: Context = Depends(get_ctx),
) -> OAuthApprovalStatusResponse:
    """Thin wrapper over the admin approval path for the approval-pending page.

    Authorization is byte-identical to ``POST /admin/oauth-clients/{id}:approve``
    / ``:deny`` (``oauth-clients:write``, org:admin implies it) and the
    decision itself is the SAME ``OAuthClientService.approve``/``deny`` calls —
    same audit records, same D7 active/approval_status coupling; this endpoint
    only translates the signed state blob into the client row. CSRF posture
    matches the consent POST: no ambient credential is honored — the browser
    must explicitly present the SPA bearer token, which a cross-site form
    cannot do.
    """
    params = verify_approval_state(ctx, body.state)
    client_id = str(params.get("client_id") or "")
    svc = OAuthClientService(ctx)
    client = await svc.get_by_client_id(client_id)
    if client is None:
        raise InvalidGrantError("unknown client in approval state")
    if body.action == "approve":
        view = await svc.approve(client.id, identity=identity)
    else:
        view = await svc.deny(client.id, identity=identity)
    logger.info(
        "oauth_client_inline_decision",
        client_id=client_id,
        action=body.action,
        actor_id=identity.sub,
    )
    response.headers["Cache-Control"] = "no-store"
    return OAuthApprovalStatusResponse(status=_approval_tri_state(view))
