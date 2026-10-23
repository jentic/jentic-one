"""Approval-in-flow endpoints behind the approval-pending page (D-approval-in-flow, P2).

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
from urllib.parse import urlencode, urlsplit

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import HTMLResponse

from jentic_one.admin.services.oauth_client_service import OAuthClientService
from jentic_one.admin.services.schemas.oauth_clients import OAuthClientView
from jentic_one.auth.services.errors import InvalidGrantError
from jentic_one.auth.web import pages
from jentic_one.auth.web.consent_state import mint_approval_state, verify_approval_state
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
from jentic_one.shared.config import effective_auth_base_url
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

    ``approved`` only when the full D7 gate passes (approved AND active), so a
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


def _safe_deny_redirect(
    client: OAuthClientView, redirect_uri: str, state: str | None
) -> str | None:
    """The access_denied redirect for the deny arm — only when provably safe.

    Safe means the request's redirect_uri is in the client's OWN registered
    set (registration already enforces https/loopback-http schemes) and parses
    as an http(s) URL. Anything else returns None and the page renders a
    terminal message instead of navigating.
    """
    if redirect_uri not in client.redirect_uris:
        return None
    if urlsplit(redirect_uri).scheme not in ("http", "https"):
        return None
    params: dict[str, str] = {"error": "access_denied"}
    if state:
        params["state"] = state
    separator = "&" if "?" in redirect_uri else "?"
    return f"{redirect_uri}{separator}{urlencode(params)}"


def render_approval_pending_page(
    request: Request,
    ctx: Context,
    *,
    client: OAuthClientView,
    response_type: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str,
    scope: str,
    state: str | None,
    nonce: str | None,
) -> HTMLResponse:
    """Render the approval-pending page for an unapproved client.

    The page carries the ORIGINAL authorize parameters in a signed
    approval-state blob (its poll + inline-decision key) and a resume URL that
    re-runs this request client-side.
    """
    approval_state = mint_approval_state(
        ctx,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        scope=scope,
        state=state,
        nonce=nonce,
    )

    # The resume leg re-runs the ORIGINAL authorize request client-side; once
    # the admin approves, this URL 302s to the IdP like any approved client.
    resume_params: dict[str, str] = {
        "response_type": response_type,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "scope": scope,
    }
    if state:
        resume_params["state"] = state
    if nonce:
        resume_params["nonce"] = nonce
    resume_url = f"/authorize?{urlencode(resume_params)}"

    base_url = effective_auth_base_url(ctx.config).rstrip("/") or str(request.base_url).rstrip("/")
    return pages.render_approval_pending_page(
        client=client,
        approval_state=approval_state,
        resume_url=resume_url,
        deny_redirect=_safe_deny_redirect(client, redirect_uri, state),
        base_url=base_url,
    )


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
