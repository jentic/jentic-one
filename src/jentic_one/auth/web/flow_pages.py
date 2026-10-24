"""Flow pages that need a signed blob minted before they can render.

:mod:`jentic_one.auth.web.pages` only renders: every signed blob it embeds is
passed in. The two pages here need one minted first — the approval-pending
page carries an ``approval`` blob keyed to the original authorize request, and
the pending-agent awaiting page an ``agent-status`` blob bound to the consent
handle and the agent. This module pairs the mint
(:mod:`jentic_one.auth.web.consent_state`) with the render and resolves the
page's absolute base URL, so more than one router can reach those pages
without importing a sibling router.

Also here: the create-form footer wording, shared by the consent page's
zero-agents render and the create submit's validation re-render, so the two
cannot drift apart on what the submit will do.
"""

from __future__ import annotations

from urllib.parse import urlencode, urlsplit

from fastapi import Request
from fastapi.responses import HTMLResponse

from jentic_one.admin.services.schemas.oauth_clients import OAuthClientView
from jentic_one.auth.services.authorize_service import AuthorizeService
from jentic_one.auth.web import pages
from jentic_one.auth.web.consent_state import mint_agent_status_state, mint_approval_state
from jentic_one.shared.config import effective_auth_base_url
from jentic_one.shared.context import Context


def page_base_url(request: Request, ctx: Context) -> str:
    """The absolute origin a flow page builds its own links against.

    The deployment's configured auth base URL wins; a deployment that has not
    pinned one falls back to this request's origin.
    """
    return effective_auth_base_url(ctx.config).rstrip("/") or str(request.base_url).rstrip("/")


def safe_deny_redirect(client: OAuthClientView, redirect_uri: str, state: str | None) -> str | None:
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


def mint_and_render_approval_pending_page(
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
    """Render the approval-pending page for a registered-but-unapproved client.

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

    return pages.render_approval_pending_page(
        client=client,
        approval_state=approval_state,
        resume_url=resume_url,
        deny_redirect=safe_deny_redirect(client, redirect_uri, state),
        base_url=page_base_url(request, ctx),
    )


def mint_and_render_agent_awaiting_page(
    params: dict[str, object],
    request: Request,
    ctx: Context,
    *,
    consent_token: str,
    agent_id: str,
    agent_name: str,
) -> HTMLResponse:
    """Render the awaiting page with a status blob bound to this handle + agent."""
    return pages.render_agent_awaiting_page(
        params,
        consent_token=consent_token,
        status_state=mint_agent_status_state(ctx, consent_token=consent_token, agent_id=agent_id),
        agent_name=agent_name,
        base_url=page_base_url(request, ctx),
    )


async def approval_note_for_user(user_id: str | None, authorize_svc: AuthorizeService) -> str:
    """The create-form footer note, truthful about the arm the submit will take.

    A resolved user gets the ACTIVE/PENDING wording its effective permissions
    earn — the same ``agents:write`` math the submit re-runs. ``None`` means
    deferred provisioning has not created the user row yet, so the arm is
    undecidable here and the neutral wording is the only honest one.

    One function for both the consent page's zero-agents render and the create
    submit's validation re-render, so the two pages cannot disagree.
    """
    if user_id is None:
        return pages.CREATE_NOTE_NEUTRAL
    if await authorize_svc.user_can_create_active_agent(user_id):
        return pages.CREATE_NOTE_ACTIVE
    return pages.CREATE_NOTE_PENDING
