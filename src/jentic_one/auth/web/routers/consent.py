"""OAuth consent endpoints: the consent page and its submit.

Flow overview:
  GET /oauth/consent   — render the consent screen for a consent handle
                         (``ch``) written by the IdP callback or the local
                         login submit
  POST /oauth/consent  — verify + burn the handle, issue the authorization
                         code, redirect to the client

``consent_model='agent'`` clients get the agent-picker variant; its
zero-agents arms (the inline create form, the pending-agent awaiting page,
the terminal empty states) and their endpoints are described in
``routers/consent_agent.py``.
"""

from __future__ import annotations

from urllib.parse import urlencode

import structlog
from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from jentic_one.admin.services.schemas.oauth_clients import OAuthClientView
from jentic_one.auth.services.authorize_service import AuthorizeService, candidate_agent_scopes
from jentic_one.auth.services.errors import InvalidGrantError, UserNotAdmittedError
from jentic_one.auth.web import pages
from jentic_one.auth.web.consent_state import (
    claims_from_params,
    consent_user_key,
    consume_consent_handle,
    load_consent_handle,
    mint_agent_create_state,
)
from jentic_one.auth.web.deps import get_authorize_service
from jentic_one.auth.web.flow import (
    check_rate_limit,
    client_gate_passes,
    error_redirect,
    get_cached_oauth_client,
    is_platform_client,
)
from jentic_one.auth.web.flow_pages import (
    approval_note_for_user,
    mint_and_render_agent_awaiting_page,
)
from jentic_one.shared.auth.permission_catalog import compute_implies_transitive
from jentic_one.shared.context import Context
from jentic_one.shared.models.oauth_clients import OAuthConsentModel
from jentic_one.shared.web import openapi_examples as ex
from jentic_one.shared.web.deps import get_ctx
from jentic_one.shared.web.sensitive import SENSITIVE

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.get(
    "/oauth/consent",
    response_class=HTMLResponse,
    dependencies=[Depends(check_rate_limit)],
    include_in_schema=False,
)
async def consent_page(
    request: Request,
    ch: str = Query(
        ..., description="Opaque consent-flow handle", openapi_examples=ex.example(ex.OPAQUE_TOKEN)
    ),
    ctx: Context = Depends(get_ctx),
    authorize_svc: AuthorizeService = Depends(get_authorize_service),
) -> HTMLResponse:
    """Display the OAuth consent screen."""
    params = await load_consent_handle(ch, request)
    if params is None:
        return pages.render_invalid_consent_page()

    scope = str(params.get("scope") or "openid")

    client_id = str(params.get("client_id") or "")
    oauth_client: OAuthClientView | None = None
    if client_id and not is_platform_client(client_id, ctx):
        oauth_client = await get_cached_oauth_client(request, client_id, ctx)
    if oauth_client is not None and oauth_client.consent_model == OAuthConsentModel.AGENT.value:
        return await _render_agent_consent_page(
            params,
            request,
            oauth_client=oauth_client,
            consent_token=ch,
            authorize_svc=authorize_svc,
            ctx=ctx,
        )

    scopes = [s.strip() for s in scope.split() if s.strip()]
    implied_by_others: set[str] = set()
    for s in scopes:
        implied_by_others.update(compute_implies_transitive(s))
    visible_scopes = [s for s in scopes if s not in implied_by_others]
    return pages.render_user_consent_page(params, consent_token=ch, visible_scopes=visible_scopes)


async def _render_agent_consent_page(
    params: dict[str, object],
    request: Request,
    *,
    oauth_client: OAuthClientView,
    consent_token: str,
    authorize_svc: AuthorizeService,
    ctx: Context,
) -> HTMLResponse:
    """The agent-picker consent variant for ``consent_model='agent'`` clients.

    Lists only the consenting user's own ``status='active'`` agents; the
    zero-active-agents arm fans out four ways:

    - handle names no provisionable subject (malformed) → the terminal empty
      state, first-run copy;
    - the user's newest agent sits in PENDING → the awaiting-approval page
      (poll + auto-continue), so a flow re-entry mid-approval is not a dead
      end;
    - the user owns only disabled/archived/rejected agents → the terminal
      empty state with the out-of-service copy (the create form must not
      sidestep the admin action that took them out of service);
    - a genuinely first-run user (zero agent rows, ever) → the inline
      create-agent form, its footer worded per the ``agents:write`` arm the
      submit will take (neutral when the user is not provisioned yet — the
      definitive check runs post-provisioning on the POST).

    The user identity is resolved read-only: rendering consent must not
    create a user row (the Deny contract); provisioning happens only on the
    create submit, an affirmative user action.

    The arm decision stays in the web layer on purpose: each arm IS a
    different page, so there is nothing to hand back to a renderer but the
    page itself. The reads it fans out on are all
    :class:`AuthorizeService` methods, and the rendered output of all four
    arms is pinned byte-for-byte by
    ``tests/unit/auth/web/test_authorize_pages_golden.py``.
    """
    app_name = str(params.get("client_name") or "Unknown Application")
    scope = str(params.get("scope") or "")

    claims = claims_from_params(params)
    raw_local_user_id = params.get("local_user_id")
    if raw_local_user_id:
        # Local-login rejoin (#1276): the user is already resolved — the
        # handle carries the authenticated user id, no claims to re-resolve.
        user_id: str | None = str(raw_local_user_id)
    else:
        user_id = await authorize_svc.resolve_existing_user_id(claims) if claims else None
    agents = await authorize_svc.list_consentable_agents(user_id) if user_id else []
    if not agents:
        # The resolved-user arms come FIRST: the awaiting page and the
        # out-of-service empty state depend only on the user's agent rows,
        # not on whether a create blob could be minted for this handle (a
        # re-entry with, say, an unverified email must still park a pending
        # agent's owner on the awaiting page rather than lie "first run").
        if user_id is not None:
            pending = await authorize_svc.newest_pending_agent(user_id)
            if pending is not None:
                # Mid-approval re-entry (or the inline create's pending arm
                # revisited): park on the awaiting page for the newest
                # pending agent instead of a dead end — the poll blob is
                # bound to this handle + this agent, both server-resolved.
                return mint_and_render_agent_awaiting_page(
                    params,
                    request,
                    ctx,
                    consent_token=consent_token,
                    agent_id=pending.id,
                    agent_name=pending.name,
                )
            if await authorize_svc.owner_has_any_agents(user_id):
                # Zero ACTIVE agents, none pending, but non-active rows exist
                # (disabled, archived, rejected): not a first-run user.
                # Offering the create form here would let the owner mint a
                # fresh ACTIVE agent and sidestep the admin action that took
                # the others out of service — keep the terminal empty state
                # (fail closed), with copy that owns up to it.
                return pages.render_no_agents_page(app_name, unavailable=True)
        user_key = consent_user_key(params)
        if user_key is None:
            # No provisionable subject on the handle (no local user, no
            # verified IdP claims): nothing to create for, so the terminal
            # empty state rather than a create form.
            return pages.render_no_agents_page(app_name)
        # The footer tells the truth about the arm the submit will take —
        # same helper the submit's validation re-render uses, so the two
        # renders of this form cannot disagree.
        approval_note = await approval_note_for_user(user_id, authorize_svc)
        create_state = mint_agent_create_state(ctx, consent_token=consent_token, user_key=user_key)
        return pages.render_agent_create_page(
            params,
            consent_token=consent_token,
            create_state=create_state,
            approval_note=approval_note,
        )

    requested = [s.strip() for s in scope.split() if s.strip()]
    allowlist = (
        frozenset(oauth_client.allowed_scopes) if oauth_client.allowed_scopes is not None else None
    )
    # The candidate set shown per agent, from the SAME function the grant's
    # scope math starts from, so the page cannot advertise a scope the grant
    # would drop. Granted/lacking marking against each agent's live scopes
    # happens in pages.render_agent_options.
    candidates = candidate_agent_scopes(requested, allowlist)

    return pages.render_agent_consent_page(
        params, consent_token=consent_token, agents=agents, candidate_scopes=candidates
    )


@router.post("/oauth/consent", dependencies=[Depends(check_rate_limit)], include_in_schema=False)
async def consent_submit(
    request: Request,
    consent_token: str = Form(
        ...,
        json_schema_extra=SENSITIVE,
        description="Single-use consent handle embedded in the consent page.",
        examples=[ex.OPAQUE_TOKEN],
    ),
    action: str = Form(
        ...,
        description="`approve` or `deny`; any value other than `deny` is treated as approval.",
        examples=["approve"],
    ),
    agent_id: str | None = Form(
        default=None,
        description=(
            "ID of the agent to connect the client to; sent only by the agent-picker consent page."
        ),
        examples=[ex.AGENT_ID],
    ),
    ctx: Context = Depends(get_ctx),
    authorize_svc: AuthorizeService = Depends(get_authorize_service),
) -> RedirectResponse:
    """Process the consent form submission. Mints the auth code only on approval.

    ``consent_token`` is the opaque handle emitted by the callback. It never
    leaves the state backend as anything more than an ID — the actual consent
    parameters (user_id, email, scopes, redirect_uri) live server-side and
    can't be tampered with or captured from browser history/proxy logs.

    ``agent_id`` is posted only by the agent-picker variant
    (``consent_model='agent'`` clients); it is validated and the scope math
    recomputed entirely server-side — the browser's selection is never
    trusted.
    """
    params = await load_consent_handle(consent_token, request)
    if params is None:
        logger.warning("oauth_consent_invalid_handle")
        return RedirectResponse(url="/error?error=invalid_consent", status_code=302)

    if not await consume_consent_handle(consent_token, request):
        logger.warning("oauth_consent_handle_replay", handle=consent_token[:8])
        return RedirectResponse(url="/error?error=invalid_consent", status_code=302)

    redirect_uri = str(params.get("redirect_uri") or "")
    raw_state = params.get("original_state")
    original_state = str(raw_state) if raw_state else None
    client_id = str(params.get("client_id") or "")
    scope = str(params.get("scope") or "openid")

    oauth_client: OAuthClientView | None = None
    if not is_platform_client(client_id, ctx):
        # Mid-flow client-gate re-check (see oauth_callback): a client denied between
        # the consent page render and this submit must not provision a user
        # row or mint a code — even on the Deny arm we return the human error
        # rather than an OAuth redirect the denied client could observe.
        oauth_client = await get_cached_oauth_client(request, client_id, ctx)
        if oauth_client is None or not client_gate_passes(oauth_client):
            logger.warning(
                "oauth_client_gate_failed_midflow", client_id=client_id, stage="consent_submit"
            )
            return RedirectResponse(url="/error?error=access_denied", status_code=302)

    if action == "deny":
        # No user_id yet because provisioning is deferred to approve; audit
        # against the user's IdP email so the deny is still attributable.
        deny_email = str(params.get("user_email") or "")
        logger.info("oauth_consent_denied", client_id=client_id, email=deny_email)
        return error_redirect(redirect_uri, "access_denied", original_state)

    idp_claims = claims_from_params(params)
    local_user_id = params.get("local_user_id")
    if local_user_id:
        # Local-login rejoin (#1276): the user authenticated against the
        # first-party account store, so the row already exists — there is
        # nothing to provision and the Deny-leaves-no-residue contract holds
        # trivially (login never creates rows).
        user_id = str(local_user_id)
    elif idp_claims is None:
        logger.warning("oauth_consent_missing_claims", client_id=client_id)
        return RedirectResponse(url="/error?error=invalid_consent", status_code=302)
    else:
        try:
            user_id = await authorize_svc.provision_from_claims(idp_claims)
        except UserNotAdmittedError:
            logger.warning("oauth_user_not_admitted", client_id=client_id)
            return RedirectResponse(url="/error?error=access_denied", status_code=302)
        except InvalidGrantError:
            logger.warning("oauth_provision_failed", client_id=client_id, exc_info=True)
            return RedirectResponse(url="/error?error=server_error", status_code=302)

    code_challenge = str(params.get("code_challenge") or "")
    raw_nonce = params.get("nonce")
    nonce = str(raw_nonce) if raw_nonce else None

    if oauth_client is not None and oauth_client.consent_model == OAuthConsentModel.AGENT.value:
        # Consent→agent binding: validate the posted agent server-side
        # (exists + active + owned by the consenting user — the picker's
        # option list IS that predicate), recompute the scope
        # intersection, mint the grant row (+ audit + oauth_grant.created),
        # and stamp grant_id on the code. Validation failures render the
        # human error page — never an OAuth redirect with a code.
        approval = await authorize_svc.approve_agent_consent(
            user_id=user_id,
            client_id=client_id,
            client_name=oauth_client.name,
            allowed_scopes=oauth_client.allowed_scopes,
            agent_id=agent_id,
            scope=scope,
        )
        if approval.error is not None:
            return RedirectResponse(url=f"/error?error={approval.error}", status_code=302)

        platform_code = await authorize_svc.issue_authorization_code(
            user_id=user_id,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            scopes=" ".join(approval.scopes),
            nonce=nonce,
            grant_id=approval.grant_id,
        )
    else:
        if not is_platform_client(client_id, ctx):
            await authorize_svc.record_consent_decision(
                user_id=user_id,
                oauth_client_id=client_id,
                approved=True,
                scopes=scope,
            )

        platform_code = await authorize_svc.issue_authorization_code(
            user_id=user_id,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            scopes=scope,
            nonce=nonce,
        )

    logger.info("oauth_consent_approved", client_id=client_id)
    redirect_params: dict[str, str] = {"code": platform_code}
    if original_state:
        redirect_params["state"] = original_state

    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(
        url=f"{redirect_uri}{separator}{urlencode(redirect_params)}", status_code=302
    )
