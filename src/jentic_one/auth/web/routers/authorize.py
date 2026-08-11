"""AuthCode+PKCE authorization endpoints."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse

from jentic_one.auth.services.authorize_service import AuthorizeService
from jentic_one.auth.services.errors import InvalidGrantError, UserNotAdmittedError
from jentic_one.shared.context import Context
from jentic_one.shared.web.deps import get_ctx

router = APIRouter()


def _is_allowed_redirect_uri(redirect_uri: str, canonical_base_url: str) -> bool:
    """Validate redirect_uri against the platform's canonical origin.

    Allows any path under the canonical base URL origin. If no canonical URL is
    configured, rejects all redirect URIs (fail-closed).
    """
    if not canonical_base_url:
        return False
    parsed_redirect = urlparse(redirect_uri)
    parsed_canonical = urlparse(canonical_base_url)
    if not parsed_redirect.scheme or not parsed_redirect.netloc:
        return False
    return (
        parsed_redirect.scheme == parsed_canonical.scheme
        and parsed_redirect.netloc == parsed_canonical.netloc
    )


def get_authorize_service(ctx: Context = Depends(get_ctx)) -> AuthorizeService:
    return AuthorizeService(ctx)


STATE_MAX_AGE_SECONDS = 600


def _sign_state(payload: dict[str, str | None], secret: str) -> str:
    """Encode and HMAC-sign state parameters for the IdP redirect."""
    data = urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = hmac.HMAC(secret.encode(), data.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{data}.{sig}"


def _verify_state(state_str: str, secret: str) -> dict[str, str | None]:
    """Verify and decode a signed state string."""
    parts = state_str.rsplit(".", 1)
    if len(parts) != 2:
        raise InvalidGrantError("invalid state")
    data, sig = parts
    expected = hmac.HMAC(secret.encode(), data.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected):
        raise InvalidGrantError("state signature invalid")
    payload: dict[str, str | None] = json.loads(urlsafe_b64decode(data))
    iat = payload.get("iat")
    if iat is not None:
        age = time.time() - float(iat)
        if age > STATE_MAX_AGE_SECONDS or age < 0:
            raise InvalidGrantError("state expired")
    return payload


@router.get("/authorize")
async def authorize_endpoint(
    request: Request,
    response_type: str = Query(...),
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    code_challenge: str = Query(...),
    code_challenge_method: str = Query(...),
    scope: str = Query(default="openid"),
    state: str | None = Query(default=None),
    nonce: str | None = Query(default=None),
    ctx: Context = Depends(get_ctx),
    authorize_svc: AuthorizeService = Depends(get_authorize_service),
) -> RedirectResponse:
    """RFC 6749 Authorization endpoint with PKCE (S256 only).

    If an external IdP is configured, redirects to the upstream provider.
    Otherwise returns an error (direct login requires a separate credential exchange).
    """
    if not _is_allowed_redirect_uri(redirect_uri, ctx.config.auth.canonical_base_url):
        return RedirectResponse(url="/error?error=invalid_redirect_uri", status_code=302)

    if response_type != "code":
        return _error_redirect(redirect_uri, "unsupported_response_type", state)

    if code_challenge_method != "S256":
        return _error_redirect(redirect_uri, "invalid_request", state, "only S256 is supported")

    callback_uri = str(request.url_for("authorize_oauth_callback"))

    internal_state = _sign_state(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "scope": scope,
            "nonce": nonce,
            "original_state": state,
            "iat": str(int(time.time())),
        },
        ctx.config.admin.auth.jwt_secret.get_secret_value(),
    )

    idp_url = authorize_svc.get_authorize_redirect_url(
        state=internal_state,
        nonce=nonce or secrets.token_urlsafe(16),
        redirect_uri=callback_uri,
    )

    if idp_url is None:
        return _error_redirect(
            redirect_uri, "server_error", state, "no identity provider configured"
        )

    return RedirectResponse(url=idp_url, status_code=302)


@router.get(
    "/oauth/callback",
    operation_id="authorizeOauthCallback",
    name="authorize_oauth_callback",
)
async def oauth_callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
    ctx: Context = Depends(get_ctx),
    authorize_svc: AuthorizeService = Depends(get_authorize_service),
) -> RedirectResponse:
    """External IdP callback — exchanges upstream code and issues platform auth code."""
    try:
        params = _verify_state(state, ctx.config.admin.auth.jwt_secret.get_secret_value())
    except InvalidGrantError:
        return RedirectResponse(url="/error?error=invalid_state", status_code=302)

    client_id = params.get("client_id", "")
    original_redirect_uri = params.get("redirect_uri", "")
    code_challenge = params.get("code_challenge", "")
    scope = params.get("scope", "openid")
    nonce = params.get("nonce")
    original_state = params.get("original_state")

    callback_uri = str(request.url_for("authorize_oauth_callback"))
    try:
        platform_code = await authorize_svc.handle_idp_callback(
            code=code,
            redirect_uri=callback_uri,
            client_id=client_id or "",
            original_redirect_uri=original_redirect_uri or "",
            code_challenge=code_challenge or "",
            scopes=scope or "openid",
            nonce=nonce,
        )
    except UserNotAdmittedError:
        # Authenticated by the IdP, but the deployment's admission policy declined
        # to provision this account. Distinct from a grant/exchange failure.
        return RedirectResponse(url="/error?error=access_denied", status_code=302)
    except (InvalidGrantError, httpx.HTTPStatusError):
        return RedirectResponse(url="/error?error=server_error", status_code=302)

    redirect_params: dict[str, str] = {"code": platform_code}
    if original_state:
        redirect_params["state"] = original_state

    separator = "&" if "?" in (original_redirect_uri or "") else "?"
    return RedirectResponse(
        url=f"{original_redirect_uri}{separator}{urlencode(redirect_params)}", status_code=302
    )


@router.get("/error")
async def error_page(error: str = Query(default="unknown_error")) -> dict[str, str]:
    """Minimal error endpoint for browser-facing authorization failures."""
    return {"error": error}


def _error_redirect(
    redirect_uri: str, error: str, state: str | None, description: str | None = None
) -> RedirectResponse:
    params: dict[str, str] = {"error": error}
    if state:
        params["state"] = state
    if description:
        params["error_description"] = description
    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(url=f"{redirect_uri}{separator}{urlencode(params)}", status_code=302)
