"""Anonymous OAuth-client Dynamic Client Registration endpoint (RFC 7591 subset).

``POST /oauth-clients`` — the anonymous-DCR front door (D1). Anonymous by
design: RFC 7591 initial access tokens were considered-and-rejected for the
GUI-client path (flagship MCP clients register anonymously; the boundary lives
at admin approval + consent, not registration). The endpoint is instead:

- gated by ``server.mcp.oauth.enabled`` (default off): the gate runs at the
  route-handler level, **before** body parsing, validation, and the rate-limit
  dependency, so every request against a disabled door — well-formed,
  malformed, or over-quota — gets the same plain 404 a build without the
  route would return (indistinguishable from not-shipped);
- rate limited per-IP (``registration_rpm``/``registration_burst``) in its own
  bucket namespace (it must not share quota with /authorize's bare-IP bucket);
- only able to mint **public** (secret-less, PKCE-only) rows that land
  ``pending`` + inactive unless the D9 auto-approve policy applies.

Error responses follow RFC 7591 §3.2.2: metadata rejections — including
schema-level ones that FastAPI would render as 422 — return 400 with a
top-level ``error: invalid_client_metadata`` member.

Distinct from ``POST /register`` (the *agent* DCR endpoint), which stays
byte-identical.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from jentic_one.auth.services.errors import InvalidClientMetadataError, RateLimitExceededError
from jentic_one.auth.services.oauth_dcr_service import OAuthDcrService
from jentic_one.auth.web.ratelimit import client_ip, get_auth_backend
from jentic_one.auth.web.schemas.oauth_client_registration import (
    OAuthClientRegistrationRequest,
    OAuthClientRegistrationResponse,
)
from jentic_one.shared.context import Context
from jentic_one.shared.resilience import RateLimiter
from jentic_one.shared.web.deps import get_ctx

logger = structlog.get_logger(__name__)

#: Raw request-body ceiling. The field-level caps bound the *accepted* payload
#: to well under 64 KiB (20 URIs x 2048 + scope 6500 + names 255), but nothing
#: else stops starlette reading a multi-MB junk body into memory before
#: validation rejects it. Declared-length check only (a chunked body without
#: Content-Length is the deployment proxy's job to bound) — cheap belt for an
#: anonymous endpoint, not a substitute for the proxy cap.
_MAX_BODY_BYTES = 64 * 1024


def _rfc7591_error(status_code: int, description: str) -> JSONResponse:
    """RFC 7591 §3.2.2 registration error body (top-level ``error`` member)."""
    return JSONResponse(
        status_code=status_code,
        content={"error": "invalid_client_metadata", "error_description": description},
    )


def _validation_error_description(exc: RequestValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(piece) for piece in err.get("loc", ()) if piece != "body")
        msg = err.get("msg", "invalid value")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts) or "invalid client metadata"


class _Rfc7591Route(APIRoute):
    """Route class owning the disabled-door 404 and RFC 7591 error shaping.

    Both concerns must run *outside* the FastAPI dependency/validation
    machinery: the ``enabled`` gate has to precede body parsing and the
    rate-limit dependency so the gate state is unobservable — a disabled door
    answers the framework's own route-not-found 404 on a full route match
    (F2; routing-level tells like 405 on non-registered methods are
    build-level and identical in both gate arms, same as the /mcp discovery
    gate's ``_McpDiscoveryRoute``) — and schema rejections have to be
    reshaped from FastAPI's 422 into the RFC's 400 ``invalid_client_metadata``
    (F3). Scoped to this router only — the platform Problem Details handler
    is untouched.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            ctx: Context = request.app.state.ctx
            if not ctx.config.server.mcp.oauth.enabled:
                # Mirror the framework's route-not-found body exactly so the
                # gate state stays unobservable. Runs before rate
                # limiting and body validation: a 429 or 422 would reveal the
                # gate is on.
                return JSONResponse(status_code=404, content={"detail": "Not Found"})

            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    declared = int(content_length)
                except ValueError:
                    declared = -1
                if declared > _MAX_BODY_BYTES:
                    logger.warning("oauth_client_dcr_body_too_large", declared=declared)
                    return _rfc7591_error(413, "request body too large")

            try:
                return await original(request)
            except RequestValidationError as exc:
                # RFC 7591 §3.2.2: schema-level rejections (missing/oversized
                # fields, malformed JSON) are 400 invalid_client_metadata,
                # not a framework-shaped 422.
                description = _validation_error_description(exc)
                logger.warning("oauth_client_dcr_invalid_metadata", detail=description)
                return _rfc7591_error(400, description)
            except InvalidClientMetadataError as exc:
                logger.warning("oauth_client_dcr_invalid_metadata", detail=str(exc))
                return _rfc7591_error(400, str(exc))

        return handler


router = APIRouter(route_class=_Rfc7591Route)


def get_oauth_dcr_service(ctx: Context = Depends(get_ctx)) -> OAuthDcrService:
    return OAuthDcrService(ctx)


def _get_registration_limiter(request: Request, ctx: Context) -> RateLimiter:
    limiter: RateLimiter | None = getattr(request.app.state, "_registration_limiter", None)
    if limiter is not None:
        return limiter
    cfg = ctx.config.auth.oauth_rate_limit
    backend = get_auth_backend(request)
    # Own bucket namespace: /authorize (and /oauth/token) fall back to a
    # bare-IP key when the request carries no client_id, and the shared store
    # would otherwise hand both limiters the same bucket with different
    # rate/burst params — client_id-less /authorize spam from an IP would
    # drain this endpoint's quota and vice versa.
    limiter = RateLimiter(
        backend,
        default_rpm=cfg.registration_rpm,
        burst=cfg.registration_burst,
        namespace="oauth-registration",
    )
    request.app.state._registration_limiter = limiter
    return limiter


async def _check_registration_rate_limit(request: Request, ctx: Context = Depends(get_ctx)) -> None:
    """Per-IP rate limiter for the anonymous registration endpoint.

    Unlike /authorize and /oauth/token there is no client_id in the key — the
    caller is registering to *get* one, and a self-chosen key component would
    let one host sidestep the bucket.
    """
    trusted = frozenset(ctx.config.auth.oauth_rate_limit.trusted_proxies)
    ip = client_ip(request, trusted)
    limiter = _get_registration_limiter(request, ctx)
    outcome = await limiter.acquire(ip)
    if not outcome.allowed:
        raise RateLimitExceededError(retry_after=outcome.retry_after_s)


@router.post(
    "/oauth-clients",
    status_code=201,
    summary="Register OAuth client (anonymous DCR)",
    response_model=OAuthClientRegistrationResponse,
    # RFC 7591 §3.2.1: unset optional metadata (software_id, software_version,
    # application_type) is omitted from the response, never emitted as JSON
    # null — strict clients (Cursor's MCP SDK zod schema) reject nulls.
    response_model_exclude_none=True,
    dependencies=[Depends(_check_registration_rate_limit)],
    responses={
        400: {
            "description": "Invalid client metadata (RFC 7591 §3.2.2): "
            '`{"error": "invalid_client_metadata", "error_description": "..."}`.'
        },
    },
)
async def register_oauth_client_endpoint(
    body: OAuthClientRegistrationRequest,
    response: Response,
    ctx: Context = Depends(get_ctx),
    dcr_svc: OAuthDcrService = Depends(get_oauth_dcr_service),
) -> OAuthClientRegistrationResponse:
    """Register a public OAuth client anonymously (RFC 7591 subset).

    Returns 201 with the new ``client_id``, or 200 with the **existing** row's
    ``client_id`` on an exact dedupe match (D8, extended per G13/#1251):
    (``software_id`` + redirect-URI set), falling back to (``client_name`` +
    redirect-URI set) for registrations without a ``software_id`` — so a
    pending client's awaiting-approval retry loop re-attaches instead of
    minting duplicate rows. No client_secret is ever issued here and no
    registration_access_token
    is returned (D12). New rows await admin approval unless the deployment
    auto-approves registrations (D9). The ``server.mcp.oauth.enabled`` gate
    lives on the route class — a disabled door 404s before this handler,
    its body validation, or the rate limiter ever run.
    """
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
