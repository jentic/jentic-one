"""AuthCode+PKCE authorization endpoints.

Flow overview:
  GET /authorize        — validate client + redirect_uri, redirect to IdP
                          (a pending-approval client renders the
                          approval-pending page instead — see
                          ``routers/approval.py``); with a valid session
                          continuation (``sc``, #1299) skip the identity rungs
                          and rejoin at consent directly
  GET /oauth/callback   — verify IdP response, hand off to the consent screen
                          (``routers/consent.py``) or, for platform clients,
                          issue the code directly
  GET /error            — minimal browser-facing error endpoint
  POST /oauth/token     — exchange code + PKCE verifier + client_secret for
                          tokens (``routers/oauth.py``)
"""

from __future__ import annotations

import hashlib
import secrets
import time
from urllib.parse import urlencode

import httpx
import structlog
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from jentic_one.admin.services.errors import UserNotFoundError
from jentic_one.admin.services.user_service import UserService
from jentic_one.auth.services.authorize_service import AuthorizeService
from jentic_one.auth.services.errors import InvalidGrantError, UserNotAdmittedError
from jentic_one.auth.web.deps import get_authorize_service
from jentic_one.auth.web.flow import (
    SESSION_CONTINUATION_MAX_AGE_SECONDS,
    STATE_MAX_AGE_SECONDS,
    SessionContinuation,
    check_rate_limit,
    clear_idp_binding_cookie,
    client_gate_passes,
    compute_s256_code_challenge,
    derive_idp_pkce_verifier,
    error_redirect,
    get_cached_oauth_client,
    get_consent_backend,
    is_platform_client,
    mint_idp_browser_binding,
    platform_client_allows_redirect,
    resolve_identity_gate,
    set_idp_binding_cookie,
    sign_payload,
    state_signing_key,
    verify_idp_binding_cookie,
    verify_payload,
    write_idp_consent_handle,
    write_local_consent_handle,
)
from jentic_one.auth.web.flow_pages import mint_and_render_approval_pending_page
from jentic_one.shared.auth.permission_catalog import OIDC_PASSTHROUGH_SCOPES
from jentic_one.shared.config import effective_auth_base_url
from jentic_one.shared.context import Context
from jentic_one.shared.models.oauth_clients import OAuthClientApprovalStatus
from jentic_one.shared.web import openapi_examples as ex
from jentic_one.shared.web.deps import get_ctx

logger = structlog.get_logger(__name__)

router = APIRouter()


async def _is_allowed_redirect_uri(
    request: Request, redirect_uri: str, client_id: str, ctx: Context
) -> bool:
    """Validate redirect_uri against platform clients (config) or registered clients (DB).

    Platform clients are validated against their configured redirect_uris.
    Third-party clients are looked up in the oauth_clients registry.
    Unknown client_ids are rejected, and so are unapproved (pending/denied)
    rows — the approval+active gate fails closed here. A pending client that
    reaches /authorize with a VALID redirect_uri is handled before this gate,
    by the approval-pending page (``routers/approval.py``).
    """
    if is_platform_client(client_id, ctx):
        return platform_client_allows_redirect(redirect_uri, client_id, ctx)
    client = await get_cached_oauth_client(request, client_id, ctx)
    if client is None or not client_gate_passes(client):
        return False
    return redirect_uri in client.redirect_uris


async def _get_client_allowed_scopes(
    request: Request, client_id: str, ctx: Context
) -> frozenset[str] | None:
    """Return allowed scopes for a registered client, or None for platform clients."""
    if is_platform_client(client_id, ctx):
        return None
    client = await get_cached_oauth_client(request, client_id, ctx)
    if client is None or client.allowed_scopes is None:
        return None
    return frozenset(client.allowed_scopes)


def _callback_uri(request: Request, canonical_base_url: str) -> str:
    """Build the IdP callback URI (the ``redirect_uri`` sent to the IdP).

    Behind a TLS-terminating proxy (e.g. an ALB) the app sees a plain-``http``
    request, so ``request.url_for`` would emit an ``http://`` callback that no
    longer matches the ``https://`` URI registered with the IdP — the IdP then
    rejects the request. When a canonical base URL is configured we therefore
    take its scheme + host and keep only the *path* resolved by ``url_for`` (so
    a route rename still flows through). Without a canonical base URL (local
    dev) we fall back to the request-derived URL unchanged.
    """
    resolved = request.url_for("authorize_oauth_callback")
    if not canonical_base_url:
        return str(resolved)
    return f"{canonical_base_url.rstrip('/')}{resolved.path}"


@router.get(
    "/authorize",
    dependencies=[Depends(check_rate_limit)],
    response_model=None,
    include_in_schema=False,
)
async def authorize_endpoint(
    request: Request,
    response_type: str = Query(
        ...,
        description="OAuth response type; only `code` is supported.",
        openapi_examples=ex.example("code"),
    ),
    client_id: str = Query(
        ...,
        description="Client ID of the application requesting authorization.",
        openapi_examples=ex.example(ex.OAUTH_PUBLIC_CLIENT_ID),
    ),
    redirect_uri: str = Query(
        ...,
        description="Where to send the result; must be a redirect URI allowed for the client.",
        openapi_examples=ex.example(ex.REDIRECT_URI),
    ),
    code_challenge: str = Query(
        ...,
        description="PKCE code challenge derived from the client's code verifier.",
        openapi_examples=ex.example(ex.PKCE_CODE_CHALLENGE),
    ),
    code_challenge_method: str = Query(
        ...,
        description="PKCE challenge method; only `S256` is supported.",
        openapi_examples=ex.example("S256"),
    ),
    scope: str = Query(
        default="openid",
        description=(
            "Space-separated scopes requested; must stay within the client's allowed scopes."
        ),
        openapi_examples=ex.example("openid"),
    ),
    state: str | None = Query(
        default=None,
        description="Opaque client value returned unchanged on the redirect back to the client.",
        openapi_examples=ex.example(ex.OAUTH_STATE),
    ),
    nonce: str | None = Query(
        default=None,
        description="OpenID Connect nonce to bind the ID token to the client's session.",
        openapi_examples=ex.example(ex.OIDC_NONCE),
    ),
    sc: str | None = Query(
        default=None,
        description="Signed session-continuation blob minted by "
        "POST /oauth/session/continue (identity-ladder rung 1). Optional; an "
        "invalid or absent value leaves the flow byte-identical to before.",
        openapi_examples=ex.example(ex.SIGNED_FLOW_STATE),
    ),
    ctx: Context = Depends(get_ctx),
    authorize_svc: AuthorizeService = Depends(get_authorize_service),
) -> RedirectResponse | HTMLResponse:
    """RFC 6749 Authorization endpoint with PKCE (S256 only).

    If an external IdP is configured, redirects to the upstream provider.
    Otherwise returns an error (direct login requires a separate credential exchange).
    """
    # D7 approval gate, now approval-in-flow (P2): a registered-but-unapproved
    # client renders a live approval-pending page — NEVER an OAuth error
    # redirect (a hard authorize-time rejection bricks strict clients). The
    # page carries the original authorize parameters in a signed state blob,
    # polls the minimal status endpoint, offers inline approve/deny to an
    # admin browser session, and auto-continues on approval by re-running this
    # request. The initial render deliberately does not distinguish pending
    # from denied (deny is reversible); a denied client resolves through the
    # poll into the standard access_denied redirect when safe.
    if not is_platform_client(client_id, ctx):
        unapproved = await get_cached_oauth_client(request, client_id, ctx)
        if (
            unapproved is not None
            and unapproved.approval_status != OAuthClientApprovalStatus.APPROVED.value
        ):
            logger.info(
                "oauth_client_awaiting_approval_page",
                client_id=client_id,
                approval_status=unapproved.approval_status,
            )
            return mint_and_render_approval_pending_page(
                request,
                ctx,
                client=unapproved,
                response_type=response_type,
                client_id=client_id,
                redirect_uri=redirect_uri,
                code_challenge=code_challenge,
                code_challenge_method=code_challenge_method,
                scope=scope,
                state=state,
                nonce=nonce,
            )

    if not await _is_allowed_redirect_uri(request, redirect_uri, client_id, ctx):
        logger.warning(
            "oauth_invalid_redirect_uri",
            client_id=client_id,
            redirect_uri=redirect_uri,
        )
        return RedirectResponse(url="/error?error=invalid_redirect_uri", status_code=302)

    if response_type != "code":
        return error_redirect(redirect_uri, "unsupported_response_type", state)

    if code_challenge_method != "S256":
        return error_redirect(redirect_uri, "invalid_request", state, "only S256 is supported")

    # Validate requested scopes against client's allowed scopes
    allowed_scopes = await _get_client_allowed_scopes(request, client_id, ctx)
    if allowed_scopes is not None:
        requested = set(scope.split())
        excess = requested - allowed_scopes - OIDC_PASSTHROUGH_SCOPES
        if excess:
            logger.warning(
                "oauth_scope_exceeds_client_allowlist",
                client_id=client_id,
                excess=sorted(excess),
            )
            return error_redirect(
                redirect_uri, "invalid_scope", state, "requested scopes exceed allowlist"
            )

    callback_uri = _callback_uri(request, effective_auth_base_url(ctx.config))

    idp_nonce = secrets.token_urlsafe(24)
    idp_code_verifier = derive_idp_pkce_verifier(ctx, idp_nonce)
    idp_code_challenge = compute_s256_code_challenge(idp_code_verifier)
    binding = mint_idp_browser_binding()

    state_payload: dict[str, str | None] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "scope": scope,
        "nonce": nonce,
        "original_state": state,
        "iat": str(int(time.time())),
    }
    idp_state_payload: dict[str, str | None] = {
        **state_payload,
        "idp_nonce": idp_nonce,
        "bnd": binding.digest,
    }
    internal_state = sign_payload(idp_state_payload, state_signing_key(ctx), purpose="state")

    idp_url = authorize_svc.get_authorize_redirect_url(
        state=internal_state,
        nonce=idp_nonce,
        redirect_uri=callback_uri,
        code_challenge=idp_code_challenge,
    )

    # Identity dispatch: the explicit rung ladder lives in flow.py
    # (resolve_identity_gate) — platform-session continuation (#1299, the
    # ``sc`` blob minted by POST /oauth/session/continue) > IdP redirect >
    # local-login form (#1276). Rung 1 returns a verified SessionContinuation
    # and this endpoint owns the async redemption: single-use burn, then the
    # SAME rejoin arms as the local login submit (platform → direct code,
    # registered third-party → the shared consent handle). A failed
    # redemption (replay race) falls through to rungs 2/3 as if no
    # continuation had arrived.
    gate_result = resolve_identity_gate(
        ctx, idp_url=idp_url, state_payload=state_payload, session_state=sc
    )
    if isinstance(gate_result, SessionContinuation):
        redemption = await _redeem_session_continuation(
            request,
            ctx,
            gate_result,
            authorize_svc,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            scope=scope,
            state=state,
            nonce=nonce,
        )
        if redemption is not None:
            return redemption
        gate_result = resolve_identity_gate(ctx, idp_url=idp_url, state_payload=state_payload)
    if gate_result is None or isinstance(gate_result, SessionContinuation):
        # The isinstance arm is unreachable (no session_state → no rung 1);
        # it only narrows the type for the checker.
        return error_redirect(
            redirect_uri, "server_error", state, "no identity provider configured"
        )
    if idp_url is not None and gate_result.headers.get("location") == idp_url:
        set_idp_binding_cookie(gate_result, request, binding, callback_uri=callback_uri)
    return gate_result


async def _redeem_session_continuation(
    request: Request,
    ctx: Context,
    continuation: SessionContinuation,
    authorize_svc: AuthorizeService,
    *,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    scope: str,
    state: str | None,
    nonce: str | None,
) -> RedirectResponse | None:
    """Redeem a verified session continuation; ``None`` falls through to rungs 2/3.

    Single-use first: ``set_if_absent`` on a used-marker makes the first
    redemption win and every replay inside the TTL fall through to the normal
    login — a captured resume URL must not keep minting consent handles (or,
    on the platform arm, authorization codes). Then a LIVE user-row read
    (both arms): the blob carries only the opaque ``user_id`` (no PII in the
    GET query param — see F2 on the #1300 review), so the row read resolves
    the consent page's display email anyway, and re-checking ``active`` /
    ``must_change_password`` on it closes the ≤60 s window in which a user
    deactivated or password-fenced after the exchange could still redeem.
    The rejoin arms are the local login submit's, with the user pinned at
    exchange time instead of a just-checked password.
    """
    backend = get_consent_backend(request)
    digest = hashlib.sha256(continuation.token.encode()).hexdigest()
    if not await backend.set_if_absent(
        f"session-sc-used:{digest}", b"1", ttl_s=float(SESSION_CONTINUATION_MAX_AGE_SECONDS)
    ):
        logger.warning("oauth_session_continuation_replayed", client_id=client_id)
        return None

    try:
        user = await UserService(ctx).get_by_id(continuation.user_id)
    except UserNotFoundError:
        logger.warning("oauth_session_continuation_failed", reason="user_not_found")
        return None
    if not user.active or user.must_change_password:
        # Fail closed but fall through: the worst outcome of a fenced account
        # is the unchanged rung-3 login, where the fence is enforced anyway.
        logger.warning("oauth_session_continuation_failed", reason="user_fenced")
        return None

    if is_platform_client(client_id, ctx):
        # Platform client: consent-skip is a first-party trust decision, the
        # same terminal step as the local login submit's platform arm.
        platform_code = await authorize_svc.issue_authorization_code(
            user_id=continuation.user_id,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            scopes=scope,
            nonce=nonce,
        )
        logger.info(
            "oauth_session_continuation_redeemed", client_id=client_id, consent="platform-skip"
        )
        redirect_params: dict[str, str] = {"code": platform_code}
        if state:
            redirect_params["state"] = state
        separator = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(
            url=f"{redirect_uri}{separator}{urlencode(redirect_params)}", status_code=302
        )

    # Registered third-party client: the entry gate above already re-checked
    # D7 for THIS request; belt and braces here because the redemption must
    # fail closed if the cached row somehow read differently.
    oauth_client = await get_cached_oauth_client(request, client_id, ctx)
    if oauth_client is None or not client_gate_passes(oauth_client):
        logger.warning(
            "oauth_client_gate_failed_midflow", client_id=client_id, stage="session_redeem"
        )
        return None

    # The SAME consent handle the IdP callback and the local login submit
    # write (one writer owns the shape): subject = the platform user pinned
    # at exchange time. The consent page renders that identity with its
    # "Not you?" escape, so an account mismatch is recoverable.
    consent_handle = await write_local_consent_handle(
        request,
        local_user_id=continuation.user_id,
        user_email=user.email,
        redirect_uri=redirect_uri,
        original_state=state,
        client_id=client_id,
        code_challenge=code_challenge,
        scope=scope,
        nonce=nonce,
        oauth_client=oauth_client,
    )
    logger.info("oauth_session_continuation_redeemed", client_id=client_id, consent="required")
    return RedirectResponse(url=f"/oauth/consent?ch={consent_handle}", status_code=302)


@router.get(
    "/oauth/callback",
    include_in_schema=False,
    operation_id="authorizeOauthCallback",
    name="authorize_oauth_callback",
    dependencies=[Depends(check_rate_limit)],
)
async def oauth_callback(
    request: Request,
    code: str = Query(
        ...,
        description="Authorization code issued by the external identity provider.",
        openapi_examples=ex.example(ex.AUTHORIZATION_CODE),
    ),
    state: str = Query(
        ...,
        description="Signed authorization-flow state that `/authorize` sent to the provider.",
        openapi_examples=ex.example(ex.OAUTH_STATE),
    ),
    ctx: Context = Depends(get_ctx),
    authorize_svc: AuthorizeService = Depends(get_authorize_service),
) -> RedirectResponse:
    """External IdP callback — exchanges upstream code and issues platform auth code."""
    try:
        params = verify_payload(
            state,
            state_signing_key(ctx),
            purpose="state",
            max_age=STATE_MAX_AGE_SECONDS,
        )
    except InvalidGrantError:
        logger.warning("oauth_callback_invalid_state")
        return RedirectResponse(url="/error?error=invalid_state", status_code=302)

    client_id = params.get("client_id", "")
    original_redirect_uri = params.get("redirect_uri", "")
    code_challenge = params.get("code_challenge", "")
    scope = params.get("scope", "openid")
    nonce = params.get("nonce")
    original_state = params.get("original_state")
    idp_nonce = str(params.get("idp_nonce") or "") or None
    expected_bnd = str(params.get("bnd") or "") or None

    callback_uri = _callback_uri(request, effective_auth_base_url(ctx.config))

    def _finish_callback(response: RedirectResponse) -> RedirectResponse:
        clear_idp_binding_cookie(response, request, expected_bnd, callback_uri=callback_uri)
        return response

    if not idp_nonce or not verify_idp_binding_cookie(
        request, expected_bnd, callback_uri=callback_uri
    ):
        logger.warning("oauth_callback_binding_cookie_invalid", client_id=client_id)
        return RedirectResponse(url="/error?error=invalid_state", status_code=302)

    backend = get_consent_backend(request)
    state_digest = hashlib.sha256(state.encode("utf-8")).hexdigest()
    if not await backend.set_if_absent(
        f"oauth-state-used:{state_digest}",
        b"1",
        ttl_s=float(STATE_MAX_AGE_SECONDS),
    ):
        logger.warning("oauth_callback_state_replayed", client_id=client_id)
        return _finish_callback(RedirectResponse(url="/error?error=invalid_state", status_code=302))

    idp_code_verifier = derive_idp_pkce_verifier(ctx, idp_nonce)

    oauth_client = await get_cached_oauth_client(request, client_id or "", ctx)
    if oauth_client is not None and not client_gate_passes(oauth_client):
        # Mid-flow D7 re-check: the gate at /authorize entry only covers the
        # start of the signed-state window — a client denied or deactivated
        # while the user is at the IdP must not reach consent or mint a code.
        # Browser-facing human error, never an OAuth redirect to the client
        # (D7: clients can't observe browser-side rejections).
        logger.warning("oauth_client_gate_failed_midflow", client_id=client_id, stage="callback")
        return _finish_callback(RedirectResponse(url="/error?error=access_denied", status_code=302))
    # Third-party clients always require consent regardless of the client's
    # require_consent flag: consent-skip is a first-party trust decision that
    # only platform clients (configured operator-side, not admin-registered)
    # can make. The require_consent field on registered clients is retained
    # for future use but currently has no effect on the third-party path.
    needs_consent = oauth_client is not None

    if needs_consent and oauth_client is not None:
        # Do NOT provision the local user yet — third-party consent must gate
        # account creation so a "Deny" doesn't leave behind a user row and
        # external-identity link the user never approved. Exchange the IdP code
        # for claims only; the consent handle carries the claims and the
        # approve-path provisions from them.
        try:
            claims = await authorize_svc.exchange_idp_code_for_claims(
                code=code,
                redirect_uri=callback_uri,
                idp_code_verifier=idp_code_verifier,
                idp_nonce=idp_nonce,
            )
        except (InvalidGrantError, httpx.HTTPError):
            logger.warning("oauth_idp_exchange_failed", client_id=client_id, exc_info=True)
            return _finish_callback(
                RedirectResponse(url="/error?error=server_error", status_code=302)
            )

        consent_handle = await write_idp_consent_handle(
            request,
            claims=claims,
            redirect_uri=original_redirect_uri,
            original_state=original_state,
            client_id=client_id,
            code_challenge=code_challenge,
            scope=scope,
            nonce=nonce,
            oauth_client=oauth_client,
        )
        return _finish_callback(
            RedirectResponse(url=f"/oauth/consent?ch={consent_handle}", status_code=302)
        )

    try:
        platform_code, _email = await authorize_svc.handle_idp_callback_with_email(
            code=code,
            redirect_uri=callback_uri,
            client_id=client_id or "",
            original_redirect_uri=original_redirect_uri or "",
            code_challenge=code_challenge or "",
            scopes=scope or "openid",
            nonce=nonce,
            idp_code_verifier=idp_code_verifier,
            idp_nonce=idp_nonce,
        )
    except UserNotAdmittedError:
        logger.warning("oauth_user_not_admitted", client_id=client_id)
        return _finish_callback(RedirectResponse(url="/error?error=access_denied", status_code=302))
    except (InvalidGrantError, httpx.HTTPError):
        logger.warning("oauth_idp_exchange_failed", client_id=client_id, exc_info=True)
        return _finish_callback(RedirectResponse(url="/error?error=server_error", status_code=302))

    redirect_params: dict[str, str] = {"code": platform_code}
    if original_state:
        redirect_params["state"] = original_state

    separator = "&" if "?" in (original_redirect_uri or "") else "?"
    return _finish_callback(
        RedirectResponse(
            url=f"{original_redirect_uri}{separator}{urlencode(redirect_params)}", status_code=302
        )
    )


@router.get("/error")
async def error_page(
    error: str = Query(
        default="unknown_error",
        description="Error code to display; returned as-is in the response body.",
        openapi_examples=ex.example("access_denied"),
    ),
) -> dict[str, str]:
    """Minimal error endpoint for browser-facing authorization failures."""
    return {"error": error}
