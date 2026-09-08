"""Shared authorization-flow plumbing for the auth web routers.

The pieces of the ``/authorize`` flow that more than one router needs —
``authorize.py`` (the flow itself: IdP redirect, callback, consent) and
``local_login.py`` (the local-account form that rejoins that flow, #1276):

- the per-client+IP rate-limit dependency for unauthenticated flow endpoints
  (plus the approval-status poll's own per-IP bucket),
- the platform/registered client gate (D7) helpers,
- the HMAC-signed, purpose-discriminated, TTL'd internal-state tokens
  (purposes: ``state``, ``approval``, ``login``, ``session``),
- the shared-state backend accessor and the **single** consent-handle writer
  (one place owns the handle's shape — the IdP callback and the local-login
  submit both write through it),
- the browser-page security-header posture and fonts URL.

Routers keep their own templates, handlers, and read paths; only contracts
shared *across* routers live here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from urllib.parse import urlencode

import structlog
from fastapi import Depends, Request
from fastapi.responses import RedirectResponse

from jentic_one.admin.services.oauth_client_service import OAuthClientService
from jentic_one.admin.services.schemas.oauth_clients import OAuthClientView
from jentic_one.auth.core.idp import IdpClaims
from jentic_one.auth.services.errors import InvalidGrantError, RateLimitExceededError
from jentic_one.auth.web.ratelimit import client_ip, get_auth_backend
from jentic_one.shared.context import Context
from jentic_one.shared.models.oauth_clients import OAuthClientApprovalStatus
from jentic_one.shared.resilience import RateLimiter
from jentic_one.shared.state.backend import SharedStateBackend
from jentic_one.shared.web.deps import get_ctx

logger = structlog.get_logger(__name__)

STATE_MAX_AGE_SECONDS = 600
CONSENT_STATE_MAX_AGE_SECONDS = 300

#: TTL for the session-continuation blob minted by ``POST /oauth/session/continue``
#: (identity-ladder rung 1, #1299). Deliberately much shorter than the other
#: purposes: the blob is redeemed by an immediate same-page navigation, so a
#: minute covers slow browsers while keeping a captured blob nearly useless.
SESSION_CONTINUATION_MAX_AGE_SECONDS = 60

#: localStorage key the operator SPA keeps its bearer session under. The
#: approval-pending page and the login form are served from the same origin as
#: the SPA in the default (combined) deployment, so page script can present
#: that token to /me and the session-continue exchange. Kept in lockstep with
#: ``ui/src/shared/auth``.
SPA_TOKEN_STORAGE_KEY = "jentic-one.access_token"

CONSENT_SECURITY_HEADERS: dict[str, str] = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
}

FONTS_URL = (
    "https://fonts.googleapis.com/css2"
    "?family=Nunito+Sans:wght@400;500;600;700"
    "&family=Sora:wght@600;700&display=swap"
)


# --- rate limiting -------------------------------------------------------------


def _get_authorize_limiter(request: Request, ctx: Context) -> RateLimiter:
    limiter: RateLimiter | None = getattr(request.app.state, "_authorize_limiter", None)
    if limiter is not None:
        return limiter
    cfg = ctx.config.auth.oauth_rate_limit
    backend = get_auth_backend(request)
    limiter = RateLimiter(backend, default_rpm=cfg.authorize_rpm, burst=cfg.authorize_burst)
    request.app.state._authorize_limiter = limiter
    return limiter


async def check_rate_limit(request: Request, ctx: Context = Depends(get_ctx)) -> None:
    """Per-client+IP rate limiter for unauthenticated authorization endpoints.

    Keyed ``client_id:ip`` when a ``client_id`` query parameter is present,
    plain IP otherwise (the local-login routes — their client_id rides inside
    the signed state, not the query string).
    """
    trusted = frozenset(ctx.config.auth.oauth_rate_limit.trusted_proxies)
    client_id = request.query_params.get("client_id")
    ip = client_ip(request, trusted)
    key = f"{client_id}:{ip}" if client_id else ip
    limiter = _get_authorize_limiter(request, ctx)
    outcome = await limiter.acquire(key)
    if not outcome.allowed:
        raise RateLimitExceededError(retry_after=outcome.retry_after_s)


def _get_approval_status_limiter(request: Request, ctx: Context) -> RateLimiter:
    limiter: RateLimiter | None = getattr(request.app.state, "_approval_status_limiter", None)
    if limiter is not None:
        return limiter
    cfg = ctx.config.auth.oauth_rate_limit
    backend = get_auth_backend(request)
    # Own bucket namespace (mirrors the DCR door): the approval-pending page
    # polls this endpoint for minutes at a time, and a bare-IP key in the
    # shared store would otherwise collide with /authorize's fallback bucket —
    # steady polling must not drain the /authorize quota or vice versa.
    limiter = RateLimiter(
        backend,
        default_rpm=cfg.approval_status_rpm,
        burst=cfg.approval_status_burst,
        namespace="oauth-approval-status",
    )
    request.app.state._approval_status_limiter = limiter
    return limiter


async def check_approval_status_rate_limit(
    request: Request, ctx: Context = Depends(get_ctx)
) -> None:
    """Per-IP rate limiter for the anonymous approval-status poll.

    Keyed by bare IP: the only other request input is the signed state blob,
    and a self-chosen key component would let one host sidestep the bucket by
    re-minting blobs (every /authorize render hands out a fresh one).
    """
    trusted = frozenset(ctx.config.auth.oauth_rate_limit.trusted_proxies)
    ip = client_ip(request, trusted)
    limiter = _get_approval_status_limiter(request, ctx)
    outcome = await limiter.acquire(ip)
    if not outcome.allowed:
        raise RateLimitExceededError(retry_after=outcome.retry_after_s)


# --- client gate (D7) ----------------------------------------------------------


def is_platform_client(client_id: str, ctx: Context) -> bool:
    """Check if client_id is a known platform client from config."""
    return any(pc.client_id == client_id for pc in ctx.config.auth.platform_clients)


def platform_client_allows_redirect(redirect_uri: str, client_id: str, ctx: Context) -> bool:
    """Check if a platform client's config allows the given redirect_uri."""
    for pc in ctx.config.auth.platform_clients:
        if pc.client_id == client_id:
            return redirect_uri in pc.redirect_uris
    return False


async def get_cached_oauth_client(
    request: Request, client_id: str, ctx: Context
) -> OAuthClientView | None:
    """Return the OAuth client view for ``client_id``, cached per request.

    /authorize touches the same client row three times (redirect-URI validation,
    scope-allowlist check, consent decision); this collapses them into one DB
    read. ``None`` in the cache means "confirmed unknown" — a repeat lookup for
    the same client_id in the same request skips the DB round-trip.
    """
    cache: dict[str, OAuthClientView | None] | None = getattr(
        request.state, "_oauth_client_cache", None
    )
    if cache is None:
        cache = {}
        request.state._oauth_client_cache = cache
    if client_id not in cache:
        cache[client_id] = await OAuthClientService(ctx).get_by_client_id(client_id)
    return cache[client_id]


def client_gate_passes(client: OAuthClientView) -> bool:
    """The D7 client gate: only ``active`` AND ``approved`` rows may proceed.

    Checked at /authorize entry and *re-checked* mid-flow (IdP callback, local
    login submit, consent submit) so a client denied or deactivated inside the
    signed-state window cannot walk the rest of the flow to a minted code.
    """
    return client.active and client.approval_status == OAuthClientApprovalStatus.APPROVED.value


# --- identity dispatch -----------------------------------------------------------


@dataclass(frozen=True)
class SessionContinuation:
    """Rung-1 outcome: a verified platform-session continuation (#1299).

    Carries the ``user_id`` pinned at exchange time by
    ``POST /oauth/session/continue`` plus the raw blob so the caller can burn
    it (single-use). Deliberately NOT the email: the blob rides a GET query
    param, so it must hold no PII — the redemption arm re-reads the user row
    (which also gives it a live ``active`` / ``must_change_password``
    re-check) and resolves the display email there. The caller —
    ``GET /authorize`` — owns the async redemption: burn, row re-check, then
    the same consent-handle write / code issuance the local-login rejoin
    uses.
    """

    user_id: str
    token: str


#: The flow parameters a session continuation is pinned to. The blob is only
#: honored when every one of these matches the CURRENT authorize request, so a
#: continuation minted for one flow can never be spliced into another
#: (different client, redirect target, PKCE challenge, scope set, or client
#: state/nonce).
_SESSION_BOUND_FIELDS: tuple[str, ...] = (
    "client_id",
    "redirect_uri",
    "code_challenge",
    "scope",
    "nonce",
    "original_state",
)


def _verify_session_continuation(
    session_state: str, ctx: Context, state_payload: dict[str, str | None]
) -> SessionContinuation | None:
    """Verify + bind the ``sc`` blob; ``None`` (fall through) on any failure.

    Failure falls through to rungs 2/3 rather than erroring: the continuation
    is a convenience rung, and the worst outcome of a bad blob must be the
    unchanged login the user would have seen anyway (no oracle, no dead end).
    """
    try:
        payload = verify_payload(
            session_state,
            session_signing_key(ctx),
            purpose="session",
            max_age=SESSION_CONTINUATION_MAX_AGE_SECONDS,
        )
    except InvalidGrantError:
        logger.warning("oauth_session_continuation_rejected", reason="verify_failed")
        return None
    for field in _SESSION_BOUND_FIELDS:
        if payload.get(field) != state_payload.get(field):
            logger.warning("oauth_session_continuation_rejected", reason="flow_mismatch")
            return None
    user_id = str(payload.get("user_id") or "")
    if not user_id:
        logger.warning("oauth_session_continuation_rejected", reason="missing_user")
        return None
    return SessionContinuation(user_id=user_id, token=session_state)


def resolve_identity_gate(
    ctx: Context,
    *,
    idp_url: str | None,
    state_payload: dict[str, str | None],
    session_state: str | None = None,
) -> RedirectResponse | SessionContinuation | None:
    """The explicit identity-dispatch ladder for ``GET /authorize``.

    Once the request is validated and the signed internal state is minted,
    exactly one rung answers "who authenticates this human?":

    1. **Platform-session reuse** (#1299): the request carries a session
       continuation (``sc``) minted by ``POST /oauth/session/continue`` from a
       live platform bearer token. Verified here (purpose, iat/TTL, tamper,
       and flow binding) and returned as a :class:`SessionContinuation` — the
       caller owns the async redemption (single-use burn, consent-handle
       write / code issuance). Two-sided gate like rung 3: the continuation
       can only be *minted* on a local-login deployment, and mid-window
       config flips must not let one be *redeemed* on any other shape. Any
       failure falls through — without a valid continuation rungs 2/3 are
       byte-identical to before.
    2. **External IdP**: the service resolved an upstream authorize URL —
       redirect to it. IdP always wins over local login, so there is no mixed
       mode with the password form.
    3. **Local-account login form** (#1276): the deployment opted in via
       ``auth.local_login.enabled``, no IdP resolved, AND ``auth.idp.enabled``
       is false (belt and braces: a configured-but-unresolvable IdP must fail
       closed, not fall through to passwords). This rung is the ONLY mint
       site of the ``login``-purpose carry-through token — the IdP-leg
       ``state`` uses a different purpose and derived key, so it can never
       open the form.
    4. Nothing is enabled: return ``None`` — the caller renders its standard
       ``server_error`` redirect, byte-identical to the pre-ladder flow.
    """
    # Rung 1 — platform-session reuse (#1299): same two-sided config gate as
    # rung 3, because the continuation both originates from and resumes into
    # the local-login deployment shape (on IdP deployments the IdP's own SSO
    # session is the session-reuse story).
    if (
        session_state is not None
        and ctx.config.auth.local_login.enabled
        and not ctx.config.auth.idp.enabled
    ):
        continuation = _verify_session_continuation(session_state, ctx, state_payload)
        if continuation is not None:
            return continuation

    # Rung 2 — external IdP redirect.
    if idp_url is not None:
        return RedirectResponse(url=idp_url, status_code=302)

    # Rung 3 — local-account login form (gate on + no IdP, resolved OR configured).
    if ctx.config.auth.local_login.enabled and not ctx.config.auth.idp.enabled:
        login_token = sign_payload(dict(state_payload), login_signing_key(ctx), purpose="login")
        return RedirectResponse(url=f"/login?{urlencode({'ls': login_token})}", status_code=302)

    # No identity path is configured — the caller owns the error redirect.
    return None


# --- signed internal state ------------------------------------------------------


def derive_key(master_secret: str, purpose: str) -> str:
    """Derive a purpose-specific signing key from the master secret via HMAC."""
    return hmac.HMAC(
        master_secret.encode(), f"oauth-{purpose}".encode(), hashlib.sha256
    ).hexdigest()


def state_signing_key(ctx: Context) -> str:
    """The purpose-derived HMAC key for the ``/authorize`` internal state."""
    return derive_key(ctx.config.admin.auth.jwt_secret.get_secret_value(), "state")


def approval_state_key(ctx: Context) -> str:
    """Signing key for the approval-state blob.

    Same signer/mechanism as the IdP-leg ``state`` but a distinct derived key
    AND a distinct ``_purpose`` discriminator, so an approval blob can never be
    replayed into /oauth/callback (or vice versa).
    """
    return derive_key(ctx.config.admin.auth.jwt_secret.get_secret_value(), "approval")


def login_signing_key(ctx: Context) -> str:
    """Signing key for the local-login carry-through token (``ls``).

    Distinct derived key AND distinct ``_purpose`` discriminator — the same
    mutual-rejection discipline ``state``/``approval`` already have, extended
    to the third purpose. An IdP-leg ``state`` (which every IdP-bound
    /authorize hands to the browser verbatim in the redirect URL) can never
    open the login form, and a login token can never be replayed into
    /oauth/callback or the approval endpoints.
    """
    return derive_key(ctx.config.admin.auth.jwt_secret.get_secret_value(), "login")


def session_signing_key(ctx: Context) -> str:
    """Signing key for the platform-session continuation blob (``sc``, #1299).

    Fourth purpose in the matrix, same mutual-rejection discipline: only
    ``POST /oauth/session/continue`` mints it (after verifying a live platform
    bearer token) and only rung 1 of :func:`resolve_identity_gate` redeems it.
    A ``state``/``approval``/``login`` blob can never resume the flow as a
    session, and a session continuation can never open the login form, the
    IdP callback, or the approval endpoints.
    """
    return derive_key(ctx.config.admin.auth.jwt_secret.get_secret_value(), "session")


def sign_payload(payload: dict[str, str | None], secret: str, *, purpose: str) -> str:
    """Encode and HMAC-sign a payload with a purpose discriminator."""
    payload["_purpose"] = purpose
    data = urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = hmac.HMAC(secret.encode(), data.encode(), hashlib.sha256).hexdigest()
    return f"{data}.{sig}"


def verify_payload(
    token_str: str, secret: str, *, purpose: str, max_age: int
) -> dict[str, str | None]:
    """Verify and decode a signed payload, checking purpose and TTL."""
    parts = token_str.rsplit(".", 1)
    if len(parts) != 2:
        raise InvalidGrantError(f"invalid {purpose} token")
    data, sig = parts
    expected = hmac.HMAC(secret.encode(), data.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise InvalidGrantError(f"{purpose} signature invalid")
    payload: dict[str, str | None] = json.loads(urlsafe_b64decode(data))
    if payload.get("_purpose") != purpose:
        raise InvalidGrantError(f"token purpose mismatch: expected {purpose}")
    iat = payload.get("iat")
    if iat is None:
        # Every mint site stamps iat; a signed payload without one has no
        # enforceable lifetime, so absence is fatal — the TTL check must not
        # be skippable (matters most for the anonymous approval-status poll).
        raise InvalidGrantError(f"{purpose} token missing iat")
    age = time.time() - float(iat)
    if age > max_age or age < 0:
        raise InvalidGrantError(f"{purpose} token expired")
    return payload


# --- consent handle -------------------------------------------------------------


def get_consent_backend(request: Request) -> SharedStateBackend:
    return get_auth_backend(request)


async def _write_consent_handle(
    request: Request, subject: dict[str, object], common: dict[str, object]
) -> str:
    """Store a consent handle and return its key — the single shape owner.

    ``subject`` identifies the authenticated human: ``{"claims": {…}}`` from
    the IdP callback, or ``{"local_user_id": "…"}`` from the local login form.
    The consent page and submit read both variants; keeping one writer means
    the two entry paths can never drift apart in shape.
    """
    consent_handle = secrets.token_urlsafe(32)
    payload_json = json.dumps({**subject, **common, "iat": int(time.time())}).encode()
    backend = get_consent_backend(request)
    await backend.set(
        f"consent-handle:{consent_handle}",
        payload_json,
        ttl_s=float(CONSENT_STATE_MAX_AGE_SECONDS),
    )
    return consent_handle


def _consent_common(
    *,
    redirect_uri: str | None,
    original_state: str | None,
    client_id: str | None,
    code_challenge: str | None,
    scope: str | None,
    nonce: str | None,
    oauth_client: OAuthClientView,
    user_email: str,
) -> dict[str, object]:
    return {
        "redirect_uri": redirect_uri,
        "original_state": original_state,
        "client_id": client_id,
        "code_challenge": code_challenge,
        "scope": scope,
        "nonce": nonce,
        "client_name": oauth_client.name,
        "client_description": oauth_client.description,
        "user_email": user_email,
    }


async def write_idp_consent_handle(
    request: Request,
    *,
    claims: IdpClaims,
    redirect_uri: str | None,
    original_state: str | None,
    client_id: str | None,
    code_challenge: str | None,
    scope: str | None,
    nonce: str | None,
    oauth_client: OAuthClientView,
) -> str:
    """Store the IdP-callback consent handle (subject = verified IdP claims)."""
    subject: dict[str, object] = {
        "claims": {
            "external_subject": claims.external_subject,
            "email": claims.email,
            "email_verified": claims.email_verified,
            "first_name": claims.first_name,
            "last_name": claims.last_name,
        }
    }
    common = _consent_common(
        redirect_uri=redirect_uri,
        original_state=original_state,
        client_id=client_id,
        code_challenge=code_challenge,
        scope=scope,
        nonce=nonce,
        oauth_client=oauth_client,
        user_email=claims.email,
    )
    return await _write_consent_handle(request, subject, common)


async def write_local_consent_handle(
    request: Request,
    *,
    local_user_id: str,
    user_email: str,
    redirect_uri: str,
    original_state: str | None,
    client_id: str,
    code_challenge: str,
    scope: str,
    nonce: str | None,
    oauth_client: OAuthClientView,
) -> str:
    """Store the local-login consent handle (subject = already-provisioned user).

    Carries the authenticated ``local_user_id`` instead of IdP claims — the
    consent approve arm skips ``provision_from_claims`` (the user exists;
    Deny-leaves-no-row holds trivially) and the deny arm is unchanged.
    """
    subject: dict[str, object] = {"local_user_id": local_user_id}
    common = _consent_common(
        redirect_uri=redirect_uri,
        original_state=original_state,
        client_id=client_id,
        code_challenge=code_challenge,
        scope=scope,
        nonce=nonce,
        oauth_client=oauth_client,
        user_email=user_email,
    )
    return await _write_consent_handle(request, subject, common)
