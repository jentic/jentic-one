"""Server-rendered pages of the authorize/consent flow.

The page markup lives in ``templates/`` as plain ``str.format`` templates
(packaged with the wheel through the normal package include), deliberately
free of any templating library. The security posture of every page:

- static structure only — every dynamic value is HTML-escaped before it is
  formatted in, and the visual theme (``{page_css}``/``{logo_block}``) is the
  static, drift-guarded constant pair from :mod:`jentic_one.auth.web.theme`;
- page script is a static fragment (``*_script.html``) that is never
  ``str.format``ed, so its braces need no doubling; every dynamic value it
  reads
  comes from one JSON ``<script type="application/json">`` block, the single
  escaped seam between server data and page script, ``\\u003c``-escaped so
  no value can close the block;
- every response carries :data:`~jentic_one.auth.web.flow.CONSENT_SECURITY_HEADERS`.

The render functions here only render: signed flow blobs are minted by the
callers (:mod:`jentic_one.auth.web.consent_state`) and passed in; the two
pages that need a mint paired with their render have that pair in
:mod:`jentic_one.auth.web.flow_pages`.

Every template is read at import time and a missing one is deliberately
fatal: the markup IS the page, so a wheel that shipped without it could only
serve a broken consent screen. (Contrast ``assets/auth.css`` in
:mod:`jentic_one.auth.web.theme`, which degrades to an empty string because a
missing stylesheet costs only the visual theme.) The packaging regression
that would cause it is caught before release by the non-empty assertion in
``tests/unit/auth/web/test_authorize_pages_golden.py``.
"""

from __future__ import annotations

import html as html_mod
import importlib.resources
import json
from urllib.parse import urlencode, urlsplit

from fastapi.responses import HTMLResponse

from jentic_one.admin.services.schemas.oauth_clients import OAuthClientView
from jentic_one.auth.services.authorize_service import AgentConsentOption
from jentic_one.auth.web.flow import CONSENT_SECURITY_HEADERS, FONTS_URL, SPA_TOKEN_STORAGE_KEY
from jentic_one.auth.web.scope_descriptions import scope_to_permission_description
from jentic_one.auth.web.theme import AUTH_PAGE_CSS, LOGO_BLOCK_HTML


def _load_template(name: str, *, strip_final_newline: bool = False) -> str:
    """Read a packaged template once at import time.

    Full-page templates are used exactly as stored. Files interpolated INTO a
    page (the inline scripts, the agent option block) carry a POSIX final
    newline that is not part of the fragment, so callers strip it.

    An unreadable template raises, failing this module's import — see the
    module docstring for why that is the intended posture.
    """
    text = (
        importlib.resources.files("jentic_one.auth.web")
        .joinpath("templates", name)
        .read_text(encoding="utf-8")
    )
    return text.removesuffix("\n") if strip_final_newline else text


#: How often the approval-pending and agent-awaiting pages poll their status
#: endpoints. 5 s keeps a single tab at 12 rpm, well inside the endpoints' own
#: rate bucket.
APPROVAL_POLL_INTERVAL_MS = 5000

#: SPA route of the OAuth-client approval queue (Settings → queue tab), used
#: for the "ask your admin" deep link. Kept in lockstep with the UI's
#: ``oauthQueue`` link target (``ui/src/shared/lib/agentStream.tsx``) and the
#: ``/app`` SPA mount (``shared/web/static.py``).
APPROVAL_QUEUE_SPA_PATH = "/app/settings?tab=queue"

#: SPA route of the agents list (the /register flow's approval queue —
#: PENDING agents render there with approve/deny). Kept in lockstep with the
#: UI's ``ROUTES.agents`` (``ui/src/shared/app/routes.ts``) and the ``/app``
#: SPA mount (``shared/web/static.py``).
AGENTS_SPA_PATH = "/app/agents"

# The 'user' consent model page: the client's requested permissions and an
# approve/deny pair. The check-mark bullet lives in auth.css.
_CONSENT_PAGE_TEMPLATE = _load_template("consent.html")

# The approval-pending page for a registered-but-unapproved client, plus its
# poll / inline-decision / auto-continue script.
_AWAITING_APPROVAL_PAGE_TEMPLATE = _load_template("awaiting_approval.html")
_APPROVAL_PENDING_SCRIPT = _load_template("approval_pending_script.html", strip_final_newline=True)

# The agent-picker consent variant (consent_model='agent' clients only — the
# 'user' template stays separate). The redirect-URI origin is rendered
# prominently (client-claimed names are untrusted — phishing counter), and
# the permission list is replaced by an agent picker whose per-agent scope
# lists show the requested ∩ allowlist candidates: scopes the agent lacks
# render greyed-out so the user sees the ceiling.
_AGENT_CONSENT_PAGE_TEMPLATE = _load_template("agent_consent.html")
_AGENT_OPTION_TEMPLATE = _load_template("agent_option.html", strip_final_newline=True)

# The terminal zero-agents empty state, HTTP 200, no code minted: for a
# handle that names no provisionable subject, and for users whose agents were
# all disabled/archived/rejected (the create form must not sidestep that
# admin action). The two arms carry different copy because "you don't have
# one yet" is a lie for the second.
_NO_AGENTS_PAGE_TEMPLATE = _load_template("no_agents.html")

_NO_AGENTS_FIRST_RUN_HEADLINE = "you don't have one yet"
_NO_AGENTS_FIRST_RUN_DESCRIPTION = (
    "This application acts through one of your approved agents.\n"
    "            Register an agent (see the Jentic One agent-registration docs),\n"
    "            have an administrator approve it, then retry the connection\n"
    "            from your application."
)
_NO_AGENTS_UNAVAILABLE_HEADLINE = "you have no available agents"
_NO_AGENTS_UNAVAILABLE_DESCRIPTION = (
    "This application acts through one of your approved agents, but\n"
    "            none of your agents are currently available. Contact your\n"
    "            administrator to restore one, then retry the connection\n"
    "            from your application."
)

# The zero-agents create-agent form, rendered when the consent handle
# names a provisionable subject AND the resolved user owns no agent rows at
# all. No page script, so no JSON seam is needed.
_CREATE_AGENT_PAGE_TEMPLATE = _load_template("create_agent.html")

#: Footer note on the create form — set per arm so the form never promises
#: the wrong outcome. The GET render detects the arm
#: for already-provisioned users; a not-yet-provisioned IdP subject gets the
#: neutral wording because the definitive check runs post-provisioning on the
#: POST.
CREATE_NOTE_ACTIVE = (
    "The agent is created with the platform's default agent\n            permissions, owned by you."
)
CREATE_NOTE_PENDING = (
    "Your agent will need administrator approval before you can\n"
    "            continue &mdash; the next page waits and continues\n"
    "            automatically once it is approved."
)
CREATE_NOTE_NEUTRAL = (
    "The agent is owned by you and may need administrator approval\n"
    "            before you can continue."
)

# The pending-agent awaiting page (P4 hybrid): rendered when the inline
# creation lands in the PENDING posture and when a flow re-entry finds the
# user's only agents still pending. Mirrors the approval-pending page: poll +
# auto-continue + terminal deny, and the same "ask your admin" copy-link
# pattern. No inline decision panel: the consenting user lacks agents:write
# by definition, and admins decide in the SPA agents queue. Unlike the
# approval-pending script, a 400 from the poll is TERMINAL instead of
# re-minting — the status blob shares the consent handle's lifetime, so once
# it expires the continue leg is dead anyway.
_AGENT_AWAITING_PAGE_TEMPLATE = _load_template("agent_awaiting.html")
_AGENT_PENDING_SCRIPT = _load_template("agent_pending_script.html", strip_final_newline=True)

_INVALID_CONSENT_PAGE = "<html><body><h1>Invalid or expired consent request</h1></body></html>"


def _page_config_json(page_config: dict[str, object]) -> str:
    """Serialise a page-config dict for the JSON ``<script>`` seam.

    ``\\u003c``-escaped so attacker-influenced values (a redirect_uri rides
    into the approval page's deny_redirect/resume_url) can never close the
    JSON ``<script>`` block.
    """
    return json.dumps(page_config).replace("<", "\\u003c")


def _html(content: str) -> HTMLResponse:
    return HTMLResponse(content=content, headers=CONSENT_SECURITY_HEADERS)


def redirect_origin(redirect_uri: str) -> str:
    """The redirect-URI origin, rendered prominently on the agent pages.

    Client-claimed names are untrusted (a phishing counter) — the origin is
    the one client-controlled string the user can actually verify.
    """
    parts = urlsplit(redirect_uri)
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return redirect_uri


def restart_authorize_url(params: dict[str, object]) -> str:
    """Rebuild the original /authorize URL from a consent handle (no ``sc``).

    The consent page's "Not you?" escape: re-runs the SAME authorize request
    without any session continuation, so the ladder lands on rung 2/3 (IdP or
    the login form) and the user authenticates as someone else. All values
    are handle-derived (server-side state), never request input.
    """
    query: dict[str, str] = {
        "response_type": "code",
        "client_id": str(params.get("client_id") or ""),
        "redirect_uri": str(params.get("redirect_uri") or ""),
        "code_challenge": str(params.get("code_challenge") or ""),
        "code_challenge_method": "S256",
        "scope": str(params.get("scope") or "openid"),
    }
    original_state = params.get("original_state")
    if original_state:
        query["state"] = str(original_state)
    nonce = params.get("nonce")
    if nonce:
        query["nonce"] = str(nonce)
    return f"/authorize?{urlencode(query)}"


def render_invalid_consent_page() -> HTMLResponse:
    """The 400 page for an unknown or expired consent handle."""
    return HTMLResponse(
        content=_INVALID_CONSENT_PAGE, status_code=400, headers=CONSENT_SECURITY_HEADERS
    )


def render_approval_pending_page(
    *,
    client: OAuthClientView,
    approval_state: str,
    resume_url: str,
    deny_redirect: str | None,
    base_url: str,
) -> HTMLResponse:
    """Render the approval-pending page for an unapproved client.

    All dynamic values reach the page script through the one escaped JSON
    block; the client's self-chosen name only ever lands in HTML-escaped text
    nodes.
    """
    queue_url = f"{base_url}{APPROVAL_QUEUE_SPA_PATH}"
    config_json = _page_config_json(
        {
            "state": approval_state,
            "status_url": f"/oauth/approval/status?{urlencode({'st': approval_state})}",
            "decision_url": "/oauth/approval/decision",
            "me_url": "/me",
            "resume_url": resume_url,
            "deny_redirect": deny_redirect,
            "poll_ms": APPROVAL_POLL_INTERVAL_MS,
            "token_key": SPA_TOKEN_STORAGE_KEY,
        }
    )

    # M1 phishing counter (mirrors the consent page's origin block): the
    # client's NAME is self-chosen at anonymous registration, so the admin
    # panel renders verifiable registration facts next to the decision
    # buttons — registered redirect-URI origins, client_id, and software_id
    # when present. All ROW-derived (never request input) and HTML-escaped.
    origins = sorted({redirect_origin(uri) for uri in client.redirect_uris})
    client_origins = "<br>".join(html_mod.escape(origin) for origin in origins) or "&mdash;"
    software_id_row = ""
    if client.software_id:
        software_id_row = (
            f"<dt>Software ID</dt>\n                <dd>{html_mod.escape(client.software_id)}</dd>"
        )

    return _html(
        _AWAITING_APPROVAL_PAGE_TEMPLATE.format(
            app_name=html_mod.escape(client.name),
            client_origins=client_origins,
            client_id_text=html_mod.escape(client.client_id),
            software_id_row=software_id_row,
            queue_url=html_mod.escape(queue_url, quote=True),
            config_json=config_json,
            page_script=_APPROVAL_PENDING_SCRIPT,
            fonts_url=FONTS_URL,
        )
    )


def render_user_consent_page(
    params: dict[str, object], *, consent_token: str, visible_scopes: list[str]
) -> HTMLResponse:
    """Render the 'user' consent-model page listing ``visible_scopes``."""
    permission_items = "\n".join(
        f"<li>{html_mod.escape(desc)}</li>"
        for s in visible_scopes
        if (desc := scope_to_permission_description(s)) is not None
    )
    return _html(
        _CONSENT_PAGE_TEMPLATE.format(
            app_name=html_mod.escape(str(params.get("client_name") or "Unknown Application")),
            app_description=html_mod.escape(
                str(params.get("client_description") or "This application")
            ),
            user_email=html_mod.escape(str(params.get("user_email") or "unknown")),
            permission_items=permission_items,
            consent_token=html_mod.escape(consent_token),
            restart_url=html_mod.escape(restart_authorize_url(params), quote=True),
            fonts_url=FONTS_URL,
            page_css=AUTH_PAGE_CSS,
            logo_block=LOGO_BLOCK_HTML,
        )
    )


def render_agent_options(
    agents: list[AgentConsentOption],
    candidate_scopes: list[str],
) -> str:
    """Render the agent picker: one radio per active agent.

    Each agent shows the candidate scope set (requested ∩ client allowlist,
    OIDC stripped) marked granted/lacking against the agent's live permissions —
    the user sees the ceiling; the submit path recomputes the math server-side.
    """
    blocks: list[str] = []
    for idx, agent in enumerate(agents):
        items: list[str] = []
        for scope_name in candidate_scopes:
            desc = scope_to_permission_description(scope_name)
            if desc is None:
                continue
            if scope_name in agent.permissions:
                items.append(f'<li class="granted">{html_mod.escape(desc)}</li>')
            else:
                items.append(
                    f'<li class="lacking">{html_mod.escape(desc)}'
                    " &mdash; not granted (agent lacks this permission)</li>"
                )
        if not items:
            items.append('<li class="lacking">No requested permissions available</li>')
        blocks.append(
            _AGENT_OPTION_TEMPLATE.format(
                agent_id=html_mod.escape(agent.id),
                agent_name=html_mod.escape(agent.name),
                scope_items="\n        ".join(items),
                checked=" checked" if idx == 0 and len(agents) == 1 else "",
            )
        )
    return "\n".join(blocks)


def render_agent_consent_page(
    params: dict[str, object],
    *,
    consent_token: str,
    agents: list[AgentConsentOption],
    candidate_scopes: list[str],
) -> HTMLResponse:
    """Render the agent-picker consent page for ``consent_model='agent'`` clients."""
    redirect_uri = str(params.get("redirect_uri") or "")
    return _html(
        _AGENT_CONSENT_PAGE_TEMPLATE.format(
            app_name=html_mod.escape(str(params.get("client_name") or "Unknown Application")),
            app_description=html_mod.escape(
                str(params.get("client_description") or "This application")
            ),
            user_email=html_mod.escape(str(params.get("user_email") or "unknown")),
            redirect_origin=html_mod.escape(redirect_origin(redirect_uri)),
            agent_options=render_agent_options(agents, candidate_scopes),
            consent_token=html_mod.escape(consent_token),
            restart_url=html_mod.escape(restart_authorize_url(params), quote=True),
            fonts_url=FONTS_URL,
            page_css=AUTH_PAGE_CSS,
            logo_block=LOGO_BLOCK_HTML,
        )
    )


def render_no_agents_page(app_name: str, *, unavailable: bool = False) -> HTMLResponse:
    """The terminal zero-agents empty state.

    ``unavailable=True`` is the out-of-service arm — the user DOES own agents
    but every one of them sits disabled/archived/rejected, so "you don't have
    one yet" would be a lie; the copy points at the administrator instead.
    """
    return _html(
        _NO_AGENTS_PAGE_TEMPLATE.format(
            app_name=html_mod.escape(app_name),
            headline_suffix=(
                _NO_AGENTS_UNAVAILABLE_HEADLINE if unavailable else _NO_AGENTS_FIRST_RUN_HEADLINE
            ),
            description=(
                _NO_AGENTS_UNAVAILABLE_DESCRIPTION
                if unavailable
                else _NO_AGENTS_FIRST_RUN_DESCRIPTION
            ),
            fonts_url=FONTS_URL,
            page_css=AUTH_PAGE_CSS,
            logo_block=LOGO_BLOCK_HTML,
        )
    )


def render_agent_create_page(
    params: dict[str, object],
    *,
    consent_token: str,
    create_state: str,
    approval_note: str,
    agent_name: str = "",
    error: str | None = None,
) -> HTMLResponse:
    """Render the inline create-agent form with the consent-page posture.

    ``approval_note`` is one of the static ``CREATE_NOTE_*`` constants — the
    per-arm footer wording (never request-derived), so the form never promises
    an immediately-usable agent to a user whose creation will land PENDING.
    """
    redirect_uri = str(params.get("redirect_uri") or "")
    error_block = f'<div class="error" role="alert">{html_mod.escape(error)}</div>' if error else ""
    return _html(
        _CREATE_AGENT_PAGE_TEMPLATE.format(
            app_name=html_mod.escape(str(params.get("client_name") or "Unknown Application")),
            user_email=html_mod.escape(str(params.get("user_email") or "unknown")),
            redirect_origin=html_mod.escape(redirect_origin(redirect_uri)),
            error_block=error_block,
            approval_note=approval_note,
            agent_name=html_mod.escape(agent_name, quote=True),
            consent_token=html_mod.escape(consent_token, quote=True),
            create_state=html_mod.escape(create_state, quote=True),
            fonts_url=FONTS_URL,
            page_css=AUTH_PAGE_CSS,
            logo_block=LOGO_BLOCK_HTML,
        )
    )


def render_agent_awaiting_page(
    params: dict[str, object],
    *,
    consent_token: str,
    status_state: str,
    agent_name: str,
    base_url: str,
) -> HTMLResponse:
    """Render the pending-agent awaiting-approval page (P4 hybrid).

    Every dynamic value reaching the page script rides in the one escaped
    JSON block, and the agent's user-chosen name only ever lands in
    HTML-escaped text nodes. The continue leg is the fixed same-origin consent
    path for THIS handle — re-entering it re-validates handle liveness and (on
    the approve submit) the client approval+active gate server-side.
    """
    agents_url = f"{base_url}{AGENTS_SPA_PATH}"
    config_json = _page_config_json(
        {
            "status_url": f"/oauth/consent/agent/status?{urlencode({'st': status_state})}",
            "continue_url": f"/oauth/consent?{urlencode({'ch': consent_token})}",
            "poll_ms": APPROVAL_POLL_INTERVAL_MS,
        }
    )
    return _html(
        _AGENT_AWAITING_PAGE_TEMPLATE.format(
            app_name=html_mod.escape(str(params.get("client_name") or "Unknown Application")),
            agent_name=html_mod.escape(agent_name),
            user_email=html_mod.escape(str(params.get("user_email") or "unknown")),
            agents_url=html_mod.escape(agents_url, quote=True),
            config_json=config_json,
            page_script=_AGENT_PENDING_SCRIPT,
            fonts_url=FONTS_URL,
            page_css=AUTH_PAGE_CSS,
            logo_block=LOGO_BLOCK_HTML,
        )
    )
