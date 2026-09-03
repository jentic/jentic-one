"""OAuth token, revocation, introspection, and ephemeral minting endpoints."""

from __future__ import annotations

import base64
import binascii
import json as json_mod

import structlog
from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from jentic_one.admin.services.oauth_client_service import OAuthClientService
from jentic_one.auth.services.assertion_service import AssertionService
from jentic_one.auth.services.authorize_service import AuthorizeService
from jentic_one.auth.services.errors import (
    InvalidGrantError,
    InvalidRevocationRequestError,
    RateLimitExceededError,
)
from jentic_one.auth.services.oauth_revocation_service import OAuthRevocationService
from jentic_one.auth.services.service_account_auth_service import ServiceAccountAuthService
from jentic_one.auth.services.token_service import TokenService
from jentic_one.auth.web.schemas.oauth import (
    IntrospectRequest,
    IntrospectResponse,
    MintRequest,
    MintResponse,
    RevokeRequest,
    TokenRequest,
    TokenResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from jentic_one.shared.resilience import RateLimiter
from jentic_one.shared.state.backend import MemoryStateBackend, SharedStateBackend
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web.deps import get_ctx, resolve_identity

logger = structlog.get_logger(__name__)

router = APIRouter()


def _get_auth_backend(request: Request) -> SharedStateBackend:
    backend: object = getattr(request.app.state, "auth_state_backend", None)
    if isinstance(backend, SharedStateBackend):
        return backend
    logger.warning("auth_state_backend missing from app.state, using in-memory fallback")
    return MemoryStateBackend()


def _client_ip(request: Request, trusted_proxies: frozenset[str]) -> str:
    """Extract the real client IP, honoring XFF only from trusted reverse proxies."""
    socket_ip = request.client.host if request.client else "unknown"
    if not trusted_proxies or socket_ip not in trusted_proxies:
        return socket_ip
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return socket_ip
    hops = [h.strip() for h in forwarded.split(",")]
    for hop in reversed(hops):
        if hop not in trusted_proxies:
            return hop
    return socket_ip


def _get_token_limiter(request: Request, ctx: Context) -> RateLimiter:
    limiter: RateLimiter | None = getattr(request.app.state, "_token_limiter", None)
    if limiter is not None:
        return limiter
    cfg = ctx.config.auth.oauth_rate_limit
    backend = _get_auth_backend(request)
    limiter = RateLimiter(backend, default_rpm=cfg.exchange_rpm, burst=cfg.exchange_burst)
    request.app.state._token_limiter = limiter
    return limiter


async def _extract_client_id(request: Request) -> str | None:
    """Best-effort client_id extraction from the token request body."""
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type == "application/x-www-form-urlencoded":
        form = await request.form()
        value = form.get("client_id")
        return str(value) if value else None
    body_bytes = await request.body()
    if not body_bytes:
        return None
    try:
        data = json_mod.loads(body_bytes)
        return data.get("client_id") if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None


async def _check_token_rate_limit(request: Request, ctx: Context = Depends(get_ctx)) -> None:
    """Per-client+IP rate limiter for the token endpoint."""
    trusted = frozenset(ctx.config.auth.oauth_rate_limit.trusted_proxies)
    ip = _client_ip(request, trusted)
    client_id = await _extract_client_id(request)
    key = f"{client_id}:{ip}" if client_id else ip
    limiter = _get_token_limiter(request, ctx)
    outcome = await limiter.acquire(key)
    if not outcome.allowed:
        raise RateLimitExceededError(retry_after=outcome.retry_after_s)


_JWT_BEARER_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
_CLIENT_CREDENTIALS_GRANT = "client_credentials"
_AUTHORIZATION_CODE_GRANT = "authorization_code"


def get_token_service(ctx: Context = Depends(get_ctx)) -> TokenService:
    return TokenService(ctx)


def get_assertion_service(ctx: Context = Depends(get_ctx)) -> AssertionService:
    return AssertionService(ctx)


def get_sa_auth_service(ctx: Context = Depends(get_ctx)) -> ServiceAccountAuthService:
    return ServiceAccountAuthService(ctx)


def get_authorize_service(ctx: Context = Depends(get_ctx)) -> AuthorizeService:
    return AuthorizeService(ctx)


def get_oauth_client_service(ctx: Context = Depends(get_ctx)) -> OAuthClientService:
    return OAuthClientService(ctx)


def get_oauth_revocation_service(ctx: Context = Depends(get_ctx)) -> OAuthRevocationService:
    return OAuthRevocationService(ctx)


def _get_revocation_limiter(request: Request, ctx: Context) -> RateLimiter:
    limiter: RateLimiter | None = getattr(request.app.state, "_revocation_limiter", None)
    if limiter is not None:
        return limiter
    cfg = ctx.config.auth.oauth_rate_limit
    backend = _get_auth_backend(request)
    # Own bucket namespace (the 3a-2 fix-wave pattern, see the DCR door's
    # `oauth-registration` limiter): this limiter keys on the bare IP, and the
    # shared store would otherwise hand it the same bucket as another
    # bare-IP-keyed limiter with different rate/burst params. Keyed on IP
    # only — the form's client_id is caller-chosen, so including it would let
    # one host sidestep the bucket by rotating ids. Reuses the exchange
    # rpm/burst knobs (config schema unchanged): revocation traffic is
    # bounded by the same client population as /oauth/token.
    limiter = RateLimiter(
        backend,
        default_rpm=cfg.exchange_rpm,
        burst=cfg.exchange_burst,
        namespace="oauth-revocation",
    )
    request.app.state._revocation_limiter = limiter
    return limiter


async def _check_revocation_rate_limit(request: Request, ctx: Context) -> None:
    """Per-IP rate limiter for the RFC 7009 form arm (called after the gate)."""
    trusted = frozenset(ctx.config.auth.oauth_rate_limit.trusted_proxies)
    ip = _client_ip(request, trusted)
    limiter = _get_revocation_limiter(request, ctx)
    outcome = await limiter.acquire(ip)
    if not outcome.allowed:
        raise RateLimitExceededError(retry_after=outcome.retry_after_s)


def _basic_auth_credentials(request: Request) -> tuple[str, str] | None:
    """Decode HTTP Basic client credentials per RFC 6749 §2.3.1.

    Returns (client_id, client_secret) if the header is a well-formed Basic
    auth challenge, else None. Malformed headers are treated as "not provided"
    so the body-encoded fallback still runs — the caller decides how to react.
    """
    header = request.headers.get("authorization")
    if not header or not header.lower().startswith("basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    if ":" not in decoded:
        return None
    client_id, _, client_secret = decoded.partition(":")
    return client_id, client_secret


async def _parse_token_request(request: Request) -> TokenRequest:
    """Parse the token request from JSON or form-encoded body (RFC 6749 §4.1.3)."""
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type == "application/x-www-form-urlencoded":
        form = await request.form()
        data = dict(form)
        try:
            return TokenRequest.model_validate(data)
        except ValidationError as exc:
            raise InvalidGrantError(str(exc.errors()[0]["msg"])) from None
    body_bytes = await request.body()
    if not body_bytes:
        raise InvalidGrantError("request body is required")
    try:
        return TokenRequest.model_validate_json(body_bytes)
    except ValidationError as exc:
        raise InvalidGrantError(str(exc.errors()[0]["msg"])) from None


_TOKEN_REQUEST_SCHEMA = TokenRequest.model_json_schema()
_TOKEN_REQUEST_BODY: dict[str, object] = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {"schema": _TOKEN_REQUEST_SCHEMA},
            "application/x-www-form-urlencoded": {"schema": _TOKEN_REQUEST_SCHEMA},
        },
    },
}


@router.post(
    "/oauth/token",
    dependencies=[Depends(_check_token_rate_limit)],
    openapi_extra=_TOKEN_REQUEST_BODY,
)
async def token_endpoint(
    request: Request,
    response: Response,
    body: TokenRequest = Depends(_parse_token_request),
    ctx: Context = Depends(get_ctx),
    token_svc: TokenService = Depends(get_token_service),
    assertion_svc: AssertionService = Depends(get_assertion_service),
    sa_auth_svc: ServiceAccountAuthService = Depends(get_sa_auth_service),
    authorize_svc: AuthorizeService = Depends(get_authorize_service),
    oauth_client_svc: OAuthClientService = Depends(get_oauth_client_service),
) -> TokenResponse:
    """Exchange a refresh token, JWT assertion, authorization code, or client creds for tokens."""
    # RFC 6749 §5.1: token responses MUST NOT be cached by any intermediary.
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"

    # RFC 6749 §2.3.1 requires HTTP Basic support; fall back to it when the
    # client didn't post client_id/client_secret in the body.
    basic = _basic_auth_credentials(request)
    if basic is not None:
        basic_id, basic_secret = basic
        if not body.client_id:
            body.client_id = basic_id
        if not body.client_secret:
            body.client_secret = basic_secret

    if body.grant_type == _AUTHORIZATION_CODE_GRANT:
        if not body.code or not body.code_verifier or not body.redirect_uri or not body.client_id:
            raise InvalidGrantError("code, code_verifier, redirect_uri, and client_id are required")
        # Pre-validate the auth code before spending argon2 on the client secret,
        # so an unauthenticated caller can't turn junk input into 64 MiB per hit.
        await authorize_svc.precheck_auth_code(body.code)
        is_platform = any(pc.client_id == body.client_id for pc in ctx.config.auth.platform_clients)
        third_party_client_id: str | None = None
        if not is_platform:
            # Confidential clients must present the correct secret; public
            # (token_endpoint_auth_method='none') clients must present NO
            # secret and rely on PKCE alone (D5). Unapproved rows fail closed
            # (D7). PKCE stays mandatory for both — enforced above and in
            # exchange_code.
            if not await oauth_client_svc.authenticate_for_token_endpoint(
                body.client_id, body.client_secret
            ):
                raise InvalidGrantError("invalid_client")
            third_party_client_id = body.client_id
        access_token, refresh_token, id_token = await authorize_svc.exchange_code(
            code=body.code,
            code_verifier=body.code_verifier,
            redirect_uri=body.redirect_uri,
            client_id=body.client_id,
            oauth_client_id=third_party_client_id,
        )
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            id_token=id_token,
            token_type="bearer",
            expires_in=token_svc.access_ttl_seconds,
        )

    if body.grant_type == _JWT_BEARER_GRANT:
        if not body.assertion:
            raise InvalidGrantError("assertion is required for grant_type=jwt-bearer")
        access_token, refresh_token = await assertion_svc.verify_and_exchange(body.assertion)
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in=token_svc.access_ttl_seconds,
        )

    if body.grant_type == _CLIENT_CREDENTIALS_GRANT:
        if not body.client_id or not body.client_secret:
            raise InvalidGrantError("client_id and client_secret are required")
        access_token, refresh_token = await sa_auth_svc.authenticate_client_credentials(
            body.client_id, body.client_secret
        )
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in=sa_auth_svc.access_ttl_seconds,
        )

    if body.grant_type != "refresh_token":
        raise InvalidGrantError(f"unsupported grant_type: {body.grant_type}")

    if not body.refresh_token:
        raise InvalidGrantError("refresh_token is required for grant_type=refresh_token")

    verified_client_id: str | None = None
    if body.client_id and body.client_secret:
        if not await oauth_client_svc.verify_client_secret(body.client_id, body.client_secret):
            raise InvalidGrantError("invalid_client")
        verified_client_id = body.client_id
    elif body.client_id and await oauth_client_svc.is_public_client(body.client_id):
        # Public clients can't authenticate — RFC 6749 §6 still requires the
        # client_id to match the token's issuing client, which token_svc
        # enforces below (a supplied secret on a public client is rejected by
        # verify_client_secret above: NULL-hash rows short-circuit to False).
        verified_client_id = body.client_id

    access_token, refresh_token = await token_svc.refresh(
        body.refresh_token, client_id=verified_client_id
    )
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=token_svc.access_ttl_seconds,
    )


@router.post("/oauth/mint")
async def mint_endpoint(
    body: MintRequest,
    identity: Identity = get_current_identity(require_actor_type=ActorType.SERVICE_ACCOUNT),
    sa_auth_svc: ServiceAccountAuthService = Depends(get_sa_auth_service),
) -> MintResponse:
    """Mint a short-lived ephemeral token for a task agent.

    The caller must be an authenticated service account. The requested scopes
    must be a subset of the caller's own scopes.
    """
    requested_scopes = [s.strip() for s in body.scope.split() if s.strip()]
    ttl = body.ttl_seconds if body.ttl_seconds is not None else 300

    access_token = await sa_auth_svc.mint_task_token(
        host_sa_id=identity.sub,
        host_sa_scopes=identity.permissions,
        requested_scopes=requested_scopes,
        target_agent_id=body.target_agent_id,
        ttl_seconds=ttl,
    )

    return MintResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=ttl,
    )


_REVOKE_REQUEST_SCHEMA = RevokeRequest.model_json_schema()
_REVOKE_REQUEST_BODY: dict[str, object] = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {"schema": _REVOKE_REQUEST_SCHEMA},
            "application/x-www-form-urlencoded": {"schema": _REVOKE_REQUEST_SCHEMA},
        },
    },
}


@router.post(
    "/oauth/revoke",
    status_code=200,
    openapi_extra=_REVOKE_REQUEST_BODY,
    responses={
        404: {
            "description": "Form-encoded (RFC 7009) requests only: interactive OAuth "
            "for MCP is disabled (`server.mcp.oauth.enabled=false`), so the RFC 7009 "
            "arm answers the framework's plain route-not-found 404 (gate state "
            "unobservable — same posture as the anonymous DCR door). The "
            "bearer-authenticated JSON arm is not gated."
        },
        429: {
            "description": "Form-encoded (RFC 7009) requests only: per-IP rate limit "
            "exceeded (`Retry-After` header set)."
        },
    },
)
async def revoke_endpoint(
    request: Request,
    ctx: Context = Depends(get_ctx),
    token_svc: TokenService = Depends(get_token_service),
    revocation_svc: OAuthRevocationService = Depends(get_oauth_revocation_service),
) -> Response:
    """Revoke a token (RFC 7009). Always returns 200 for valid requests.

    Two client-authentication arms, negotiated on the request content type:

    - **Form-encoded** (`application/x-www-form-urlencoded`, RFC 7009 §2.1 —
      the shape MCP OAuth clients send, G11): `token` + optional
      `token_type_hint` + `client_id`. Public (secret-less) clients
      authenticate by client_id **lineage binding** — the call revokes
      anything only when the token exists and was issued to that `client_id`;
      everything else is a 200 no-op (no token-validity oracle). Revoking an
      access token kills that token only; revoking a **refresh token is a full
      disconnect** — every token of the consent grant AND the grant row itself
      die, so reconnecting requires fresh consent (deliberately beyond the
      RFC 7009 §2.1 SHOULD; one revocation semantics platform-wide). This arm
      is gated by `server.mcp.oauth.enabled` (plain 404 when off) and per-IP
      rate limited.
    - **JSON** (any other content type — the pre-G11 contract, unchanged):
      requires a platform bearer identity; revokes the caller's own token
      (access → that token, refresh → its family). Used by `jentic logout`.
    """
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()

    if content_type == "application/x-www-form-urlencoded":
        # --- RFC 7009 arm (G11) --------------------------------------------
        # Gate first, before the rate limiter and body parsing, mirroring the
        # DCR front door (§4.2): a disabled arm answers the framework's own
        # route-not-found body, and neither a 429 nor a 400 may reveal the
        # gate is on.
        if not ctx.config.server.mcp.oauth.enabled:
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        await _check_revocation_rate_limit(request, ctx)
        form = await request.form()
        token = form.get("token")
        if not token or not isinstance(token, str):
            # The endpoint's only 400 (RFC 7009 §2.2.1 invalid_request).
            raise InvalidRevocationRequestError("token is required")
        hint = form.get("token_type_hint")
        client_id = form.get("client_id")
        await revocation_svc.revoke_client_token(
            token,
            client_id=client_id if isinstance(client_id, str) and client_id else None,
            token_type_hint=hint if isinstance(hint, str) else None,
        )
        return Response(status_code=200)

    # --- legacy platform arm (pre-G11 contract, unchanged) ------------------
    # Identity first (as the old dependency order had it: a bad bearer answers
    # 401 even alongside a bad body), then the schema validation that FastAPI
    # previously ran — re-raised as RequestValidationError so the 422 shape
    # callers may rely on is preserved.
    identity = await resolve_identity(request)
    body_bytes = await request.body()
    try:
        body = RevokeRequest.model_validate_json(body_bytes)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from None
    await token_svc.revoke(body.token, identity=identity)
    return Response(status_code=200)


@router.post("/oauth/introspect")
async def introspect_endpoint(
    body: IntrospectRequest,
    identity: Identity = get_current_identity(allow_expired_password=True),
    token_svc: TokenService = Depends(get_token_service),
) -> IntrospectResponse:
    """Introspect a token (RFC 7662)."""
    result = await token_svc.introspect(body.token)
    return IntrospectResponse.model_validate(result)
