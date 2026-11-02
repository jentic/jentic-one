"""Inline agent creation on the agent-picker consent page.

Flow overview:
  POST /oauth/consent/agent         — create the consenting user's first agent
  GET /oauth/consent/agent/status   — poll a pending agent's approval

Deferred provisioning means a first-time user
reaching consent for a ``consent_model='agent'`` client owns no agents, so the
zero-agents arm renders a create-agent form: POST /oauth/consent/agent
verifies a signed, single-use ``agent-create``-purpose blob bound to the
consent handle AND the authenticated subject, re-validates the handle and the
client approval+active gate, and hands the workflow to
:meth:`AuthorizeService.create_agent_from_consent` — which re-checks the
zero-agents predicate (an agent appearing in between skips creation; a
per-subject ``set_if_absent`` marker makes parallel submits create exactly
one) and creates the agent as the consenting user through the same
``AgentService.create`` path the SPA uses (default agent scopes, same audit +
event).

The creation posture mirrors the platform's two existing doors — the SPA's
POST /agents gates on ``agents:write``, and this public mid-flow endpoint
must not out-privilege it:

- the consenting user's effective permissions include ``agents:write`` (same
  implication math as the web gate; org:admin implies it) → the agent is
  created ACTIVE and a 303 re-enters GET /oauth/consent with it pre-selected;
- otherwise → the agent is created PENDING (the ``POST /register`` posture:
  no scope grants until approval, the same requires-action event in the
  admins' queue) and an awaiting-approval page renders — it polls
  GET /oauth/consent/agent/status (anonymous, keyed by a signed
  ``agent-status`` blob bound to the consent handle and the agent id, never a
  bare id) and auto-continues into consent the moment an admin approves; a
  deny renders a terminal message. Re-entering the flow while the agent is
  still pending re-renders the awaiting page (the consent handle outlives few
  approvals, so the flow must be re-enterable); a user whose agents were all
  disabled/archived/rejected keeps a terminal empty state.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlencode

import structlog
from fastapi import APIRouter, Depends, Form, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from jentic_one.admin.services.schemas.oauth_clients import OAuthClientView
from jentic_one.auth.services.authorize_service import AuthorizeService
from jentic_one.auth.services.errors import InvalidGrantError
from jentic_one.auth.web import pages
from jentic_one.auth.web.consent_state import (
    agent_create_state_binds,
    claim_agent_create_slot,
    claims_from_params,
    consent_user_key,
    consume_agent_create_state,
    load_consent_handle,
    mint_agent_create_state,
    verify_agent_create_state,
    verify_agent_status_state,
)
from jentic_one.auth.web.deps import get_authorize_service
from jentic_one.auth.web.flow import (
    check_approval_status_rate_limit,
    check_rate_limit,
    client_gate_passes,
    get_cached_oauth_client,
    is_platform_client,
)
from jentic_one.auth.web.flow_pages import (
    approval_note_for_user,
    mint_and_render_agent_awaiting_page,
)
from jentic_one.auth.web.schemas.authorize import ConsentAgentStatusResponse
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus
from jentic_one.shared.models.oauth_clients import OAuthConsentModel
from jentic_one.shared.web import openapi_examples as ex
from jentic_one.shared.web.deps import derive_origin, get_ctx
from jentic_one.shared.web.openapi_responses import problem
from jentic_one.shared.web.sensitive import SENSITIVE

logger = structlog.get_logger(__name__)

router = APIRouter()

#: Server-side ceiling for the inline agent name — matches the SPA creation
#: path's ``AgentCreateRequest`` (min_length=1, max_length=255) and the
#: ``agents.name`` column (String(255)).
_AGENT_NAME_MAX_LENGTH = 255

_AGENT_NAME_ERROR_MESSAGE = f"Enter an agent name (1\u2013{_AGENT_NAME_MAX_LENGTH} characters)."


def _agent_tri_state(status: str | None) -> Literal["pending", "approved", "denied"]:
    """Collapse an agent row's lifecycle status into the poll tri-state.

    ``approved`` only for ACTIVE (the picker's own predicate, so the page
    never auto-continues into a consent that would not list the agent);
    ``denied`` only for the terminal REJECTED. Everything else — PENDING,
    but also DISABLED/ARCHIVED and a missing row — reads as ``pending``: the
    endpoint must not become a lifecycle or deletion oracle.
    """
    if status == ActorStatus.ACTIVE.value:
        return "approved"
    if status == ActorStatus.REJECTED.value:
        return "denied"
    return "pending"


@router.post(
    "/oauth/consent/agent",
    operation_id="consentAgentCreate",
    summary="Create the consenting user's first agent inline (consent page)",
    response_model=None,
    response_class=HTMLResponse,
    responses={
        200: {
            "description": (
                "The re-rendered consent page, as HTML for the browser that posted the "
                "form. This endpoint serves a page rather than an API body: a failure "
                "redirects (302) to `/error` instead of returning a problem document."
            ),
            "content": {
                "text/html": {
                    "schema": {
                        "type": "string",
                        "description": "The consent page HTML document.",
                    }
                }
            },
        }
    },
    dependencies=[Depends(check_rate_limit)],
)
async def consent_agent_create(
    request: Request,
    consent_token: str = Form(
        ...,
        json_schema_extra=SENSITIVE,
        description="Single-use consent handle embedded in the consent page.",
        examples=[ex.OPAQUE_TOKEN],
    ),
    create_state: str = Form(
        ...,
        json_schema_extra=SENSITIVE,
        description="Signed single-use agent-creation token embedded in the consent page.",
        examples=[ex.SIGNED_FLOW_STATE],
    ),
    agent_name: str = Form(
        ...,
        description="Name for the new agent; surrounding whitespace is trimmed.",
        examples=["invoice-reconciler"],
    ),
    ctx: Context = Depends(get_ctx),
    authorize_svc: AuthorizeService = Depends(get_authorize_service),
) -> HTMLResponse | RedirectResponse:
    """Create the consenting user's first agent from the zero-agents consent page.

    The form is rendered only when the consenting user owns zero agents in
    any status — otherwise consent is a first-run dead end. This submit
    verifies the signed single-use ``agent-create`` blob (bound to the consent
    handle AND the authenticated subject — no ambient credential is honored,
    so a cross-site form cannot drive it: it would need both the unguessable
    handle and a blob minted for that very handle), re-validates the handle
    and the client approval+active gate, provisions the user row if deferred
    provisioning left none (an affirmative user action, unlike rendering),
    re-checks the zero-agents
    predicate (an agent appearing in between skips creation — idempotent),
    creates the agent through the same ``AgentService.create`` path as the
    SPA (owner = the consenting user, default agent scopes, same audit +
    event), and 303-redirects back into ``GET /oauth/consent`` where the new
    agent renders pre-selected.

    Failure arms: expired/tampered/replayed blob and expired handle → the
    consent flow's standard ``invalid_consent`` error redirect; a gated
    client → ``access_denied``; an invalid agent name → the form re-rendered
    with the error inline and a fresh blob.

    Creation posture: the arm is decided server-side AFTER the subject is
    resolved/provisioned, against the same effective-permission math as POST
    /agents' ``agents:write`` gate. A permissioned user gets ACTIVE + a 303
    re-entry; an unpermissioned one gets a PENDING agent (the POST /register
    posture) and the awaiting-approval page. A per-subject ``set_if_absent`` slot claim
    makes N parallel submits (N distinct blobs from N renders) create exactly
    one agent — losers re-enter consent, which renders the picker or the
    awaiting page as appropriate.
    """
    try:
        blob = verify_agent_create_state(ctx, create_state)
    except InvalidGrantError:
        logger.warning("oauth_consent_agent_create_invalid_state")
        return RedirectResponse(url="/error?error=invalid_consent", status_code=302)

    if not await consume_agent_create_state(create_state, request):
        logger.warning("oauth_consent_agent_create_replayed")
        return RedirectResponse(url="/error?error=invalid_consent", status_code=302)

    params = await load_consent_handle(consent_token, request)
    if params is None:
        # Consent handle expired or unknown — same arm as the consent submit.
        logger.warning("oauth_consent_invalid_handle", stage="agent_create")
        return RedirectResponse(url="/error?error=invalid_consent", status_code=302)

    user_key = consent_user_key(params)
    if user_key is None or not agent_create_state_binds(
        blob, consent_token=consent_token, user_key=user_key
    ):
        # The blob was minted for a different handle or a different subject —
        # a spliced form fails closed with the standard error.
        logger.warning("oauth_consent_agent_create_binding_mismatch")
        return RedirectResponse(url="/error?error=invalid_consent", status_code=302)

    client_id = str(params.get("client_id") or "")
    oauth_client: OAuthClientView | None = None
    if not is_platform_client(client_id, ctx):
        # Mid-flow client-gate re-check (see consent_submit): a client denied between
        # the form render and this submit must not cause resource creation.
        oauth_client = await get_cached_oauth_client(request, client_id, ctx)
        if oauth_client is None or not client_gate_passes(oauth_client):
            logger.warning(
                "oauth_client_gate_failed_midflow", client_id=client_id, stage="agent_create"
            )
            return RedirectResponse(url="/error?error=access_denied", status_code=302)
    if oauth_client is None or oauth_client.consent_model != OAuthConsentModel.AGENT.value:
        # Only the agent-picker consent variant ever renders the create form;
        # platform and consent_model='user' clients have no zero-agents arm.
        logger.warning("oauth_consent_agent_create_wrong_consent_model", client_id=client_id)
        return RedirectResponse(url="/error?error=invalid_consent", status_code=302)

    # HTTP-level name validation BEFORE any side effect (no user row is
    # provisioned for a submit that only re-renders the form). Business
    # validation beyond the length ceiling stays in the service layer.
    name = agent_name.strip()
    if not name or len(name) > _AGENT_NAME_MAX_LENGTH:
        fresh = mint_agent_create_state(ctx, consent_token=consent_token, user_key=user_key)
        return pages.render_agent_create_page(
            params,
            consent_token=consent_token,
            create_state=fresh,
            approval_note=await _approval_note(params, authorize_svc),
            agent_name=name,
            error=_AGENT_NAME_ERROR_MESSAGE,
        )

    raw_local_user_id = params.get("local_user_id")
    result = await authorize_svc.create_agent_from_consent(
        claims=claims_from_params(params),
        local_user_id=str(raw_local_user_id) if raw_local_user_id else None,
        user_email=str(params.get("user_email") or ""),
        origin=derive_origin(request.headers.get("user-agent")),
        agent_name=name,
        client_id=client_id,
        claim_create_slot=lambda: claim_agent_create_slot(user_key, request),
    )
    if result.outcome == "error":
        return RedirectResponse(url=f"/error?error={result.error}", status_code=302)
    if result.outcome == "await_approval":
        # PENDING arm: park on the awaiting page — it polls the status
        # endpoint (blob bound to this handle + this agent) and
        # auto-continues into consent on approval.
        return mint_and_render_agent_awaiting_page(
            params,
            request,
            ctx,
            consent_token=consent_token,
            agent_id=str(result.agent_id),
            agent_name=str(result.agent_name),
        )

    # 303 (POST → GET) back into the consent page: with exactly one agent the
    # picker pre-checks it, so the user approves in the same breath (a race
    # loser or a skipped-existing submit lands on whatever the render arm
    # says — picker, awaiting page, or empty state). The handle was just
    # re-validated against the state backend, so this stays a fixed
    # same-origin path — never request-derived beyond the handle id.
    return RedirectResponse(
        url=f"/oauth/consent?{urlencode({'ch': consent_token})}", status_code=303
    )


async def _approval_note(params: dict[str, object], authorize_svc: AuthorizeService) -> str:
    """The create-form footer note for a validation re-render (read-only).

    Resolves the handle's subject the same way the consent page's zero-agents
    render does, then defers the wording to the one shared helper, so the form
    says the same thing on a first render and on a re-render. Resolution is
    read-only — a re-render must not create a user row.
    """
    raw_local_user_id = params.get("local_user_id")
    if raw_local_user_id:
        user_id: str | None = str(raw_local_user_id)
    else:
        claims = claims_from_params(params)
        user_id = await authorize_svc.resolve_existing_user_id(claims) if claims else None
    return await approval_note_for_user(user_id, authorize_svc)


@router.get(
    "/oauth/consent/agent/status",
    operation_id="consentAgentStatus",
    summary="Poll pending-agent approval status (consent awaiting page)",
    dependencies=[Depends(check_approval_status_rate_limit)],
    responses={
        400: problem("Malformed, tampered, or expired agent-status blob."),
    },
)
async def consent_agent_status_endpoint(
    response: Response,
    st: str = Query(
        ...,
        description="Signed agent-status blob minted by the consent flow's pending arm",
        openapi_examples=ex.example(ex.SIGNED_FLOW_STATE),
    ),
    ctx: Context = Depends(get_ctx),
    authorize_svc: AuthorizeService = Depends(get_authorize_service),
) -> ConsentAgentStatusResponse:
    """Minimal tri-state poll for the pending-agent awaiting page.

    Anonymous but keyed by the signed ``agent-status`` blob — never a bare
    agent id, so the endpoint cannot be used to enumerate or probe agents:
    the id it reports on is the one SIGNED into the blob, which only the
    consent flow mints, and only for an agent it verified belongs to the
    handle's subject. The response carries ONLY the tri-state — no name,
    owner, or scopes — and non-terminal lifecycle states (disabled, archived,
    a vanished row) all read as ``pending``, so possession of a blob is not a
    lifecycle oracle either. Any verification failure (bad signature, wrong
    purpose, expired ``iat``, malformed blob) is a 400 ``invalid_grant``; the
    page treats a 400 as terminal ("retry the connection") because the blob
    shares the consent handle's lifetime — a retry re-enters the flow, which
    re-parks on a fresh awaiting page while the agent stays pending. Shares
    the approval-status poll's own per-client-network rate bucket (same
    cadence, same caller shape).
    """
    params = verify_agent_status_state(ctx, st)
    status = await authorize_svc.get_agent_status(str(params.get("agent_id") or ""))
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return ConsentAgentStatusResponse(status=_agent_tri_state(status))
