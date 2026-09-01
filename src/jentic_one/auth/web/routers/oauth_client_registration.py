"""Anonymous OAuth-client Dynamic Client Registration endpoint (RFC 7591 subset).

``POST /oauth-clients`` — the phase-3a §4.2 front door (D1). Anonymous by
design: RFC 7591 initial access tokens were considered-and-rejected for the
GUI-client path (flagship MCP clients register anonymously; the boundary lives
at admin approval + consent, not registration). The endpoint is instead:

- gated by ``server.mcp.oauth.enabled`` (default off → plain 404,
  indistinguishable from not-shipped);
- rate limited per-IP (``registration_rpm``/``registration_burst``);
- only able to mint **public** (secret-less, PKCE-only) rows that land
  ``pending`` + inactive unless the D9 auto-approve policy applies.

Distinct from ``POST /register`` (the *agent* DCR endpoint), which stays
byte-identical.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from jentic_one.auth.services.errors import RateLimitExceededError
from jentic_one.auth.services.oauth_dcr_service import OAuthDcrService
from jentic_one.auth.web.routers.authorize import _client_ip, _get_auth_backend
from jentic_one.auth.web.schemas.oauth_client_registration import (
    OAuthClientRegistrationRequest,
    OAuthClientRegistrationResponse,
)
from jentic_one.shared.context import Context
from jentic_one.shared.resilience import RateLimiter
from jentic_one.shared.web.deps import get_ctx

logger = structlog.get_logger(__name__)

router = APIRouter()


def get_oauth_dcr_service(ctx: Context = Depends(get_ctx)) -> OAuthDcrService:
    return OAuthDcrService(ctx)


def _get_registration_limiter(request: Request, ctx: Context) -> RateLimiter:
    limiter: RateLimiter | None = getattr(request.app.state, "_registration_limiter", None)
    if limiter is not None:
        return limiter
    cfg = ctx.config.auth.oauth_rate_limit
    backend = _get_auth_backend(request)
    limiter = RateLimiter(backend, default_rpm=cfg.registration_rpm, burst=cfg.registration_burst)
    request.app.state._registration_limiter = limiter
    return limiter


async def _check_registration_rate_limit(request: Request, ctx: Context = Depends(get_ctx)) -> None:
    """Per-IP rate limiter for the anonymous registration endpoint.

    Unlike /authorize and /oauth/token there is no client_id in the key — the
    caller is registering to *get* one, and a self-chosen key component would
    let one host sidestep the bucket.
    """
    trusted = frozenset(ctx.config.auth.oauth_rate_limit.trusted_proxies)
    ip = _client_ip(request, trusted)
    limiter = _get_registration_limiter(request, ctx)
    outcome = await limiter.acquire(ip)
    if not outcome.allowed:
        raise RateLimitExceededError(retry_after=outcome.retry_after_s)


@router.post(
    "/oauth-clients",
    status_code=201,
    summary="Register OAuth client (anonymous DCR)",
    response_model=OAuthClientRegistrationResponse,
    dependencies=[Depends(_check_registration_rate_limit)],
)
async def register_oauth_client_endpoint(
    body: OAuthClientRegistrationRequest,
    response: Response,
    ctx: Context = Depends(get_ctx),
    dcr_svc: OAuthDcrService = Depends(get_oauth_dcr_service),
) -> OAuthClientRegistrationResponse | JSONResponse:
    """Register a public OAuth client anonymously (RFC 7591 subset, §4.2).

    Returns 201 with the new ``client_id``, or 200 with the **existing** row's
    ``client_id`` on an exact (``software_id`` + redirect-URI set) dedupe match
    (D8). No client_secret is ever issued here and no registration_access_token
    is returned (D12). New rows await admin approval unless the deployment
    auto-approves registrations (D9).
    """
    if not ctx.config.server.mcp.oauth.enabled:
        # Mirror the framework's route-not-found body exactly so a disabled
        # door is indistinguishable from a build that never shipped it (§4.2).
        return JSONResponse(status_code=404, content={"detail": "Not Found"})

    result = await dcr_svc.register(
        client_name=body.client_name,
        redirect_uris=body.redirect_uris,
        token_endpoint_auth_method=body.token_endpoint_auth_method,
        grant_types=body.grant_types,
        response_types=body.response_types,
        scope=body.scope,
        software_id=body.software_id,
        software_version=body.software_version,
        application_type=body.application_type,
    )
    if not result.created:
        response.status_code = 200
        logger.info("oauth_client_dcr_deduped", client_id=result.client_id)
    else:
        logger.info("oauth_client_dcr_registered", client_id=result.client_id)
    return OAuthClientRegistrationResponse(
        client_id=result.client_id,
        client_id_issued_at=result.client_id_issued_at,
        client_name=result.client_name,
        redirect_uris=result.redirect_uris,
        grant_types=result.grant_types,
        scope=result.scope,
        software_id=result.software_id,
        software_version=result.software_version,
        application_type=result.application_type,
    )
