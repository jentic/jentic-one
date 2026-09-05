"""Shared authorization-flow plumbing for the auth web routers.

The pieces of the ``/authorize`` flow that more than one router needs —
``authorize.py`` (the flow itself: IdP redirect, callback, consent) and
``local_login.py`` (the local-account form that rejoins that flow, #1276):

- the per-client+IP rate-limit dependency for unauthenticated flow endpoints,
- the platform/registered client gate (D7) helpers,
- the HMAC-signed, purpose-discriminated, TTL'd internal-state tokens,
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

from fastapi import Depends, Request

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

STATE_MAX_AGE_SECONDS = 600
CONSENT_STATE_MAX_AGE_SECONDS = 300

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


# --- signed internal state ------------------------------------------------------


def derive_key(master_secret: str, purpose: str) -> str:
    """Derive a purpose-specific signing key from the master secret via HMAC."""
    return hmac.HMAC(
        master_secret.encode(), f"oauth-{purpose}".encode(), hashlib.sha256
    ).hexdigest()


def state_signing_key(ctx: Context) -> str:
    """The purpose-derived HMAC key for the ``/authorize`` internal state."""
    return derive_key(ctx.config.admin.auth.jwt_secret.get_secret_value(), "state")


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
    if iat is not None:
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
