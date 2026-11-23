"""Dynamic Client Registration endpoints (RFC 7591/7592 subset)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse

from jentic_one.auth.services.errors import (
    OperationNotSupportedError,
    RateLimitExceededError,
    RegistrationAccessDeniedError,
)
from jentic_one.auth.services.registration_service import RegistrationService
from jentic_one.auth.web.ratelimit import client_rate_limit_key, get_auth_backend
from jentic_one.auth.web.schemas.registration import (
    RegisterRequest,
    RegisterResponse,
    RegistrationStatusResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.resilience import RateLimiter
from jentic_one.shared.state import StateBackendConfig, build_state_backend
from jentic_one.shared.state.backend import SharedStateBackend
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web import openapi_examples as ex
from jentic_one.shared.web.deps import get_ctx
from jentic_one.shared.web.openapi_responses import problem, too_many_requests

router = APIRouter()


def get_registration_service(ctx: Context = Depends(get_ctx)) -> RegistrationService:
    return RegistrationService(ctx)


def _resolve_auth_backend(request: Request, ctx: Context) -> SharedStateBackend:
    try:
        return get_auth_backend(request)
    except RuntimeError:
        state_backend: object = getattr(ctx, "state_backend", None)
        if isinstance(state_backend, SharedStateBackend):
            request.app.state.auth_state_backend = state_backend
            return state_backend
        backend = build_state_backend(StateBackendConfig())
        request.app.state.auth_state_backend = backend
        return backend


async def check_agent_registration_rate_limit(
    request: Request, ctx: Context = Depends(get_ctx)
) -> None:
    """Per-client-network rate limiter for anonymous agent self-registration."""
    cfg = ctx.config.auth.oauth_rate_limit
    key = client_rate_limit_key(request, cfg.trusted_networks)
    limiter = RateLimiter(
        _resolve_auth_backend(request, ctx),
        default_rpm=cfg.registration_rpm,
        burst=cfg.registration_burst,
        namespace="agent-registration",
    )
    outcome = await limiter.acquire(key)
    if not outcome.allowed:
        raise RateLimitExceededError(retry_after=outcome.retry_after_s)


def _extract_rat(request: Request) -> str:
    """Extract Registration Access Token from Authorization header."""
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise RegistrationAccessDeniedError("missing or invalid authorization header")
    return auth[7:]


@router.post(
    "/register",
    summary="Register an agent client",
    status_code=201,
    # RFC 7591 §3.2.1: optional response members without a value (claim_token
    # on the OSS single-user default — no claim-token minter installed) are
    # omitted, never emitted as JSON null. Same posture as the anonymous DCR
    # door (oauth_client_registration.py) and /oauth/token.
    response_model_exclude_none=True,
    dependencies=[Depends(check_agent_registration_rate_limit)],
    responses=too_many_requests(),
)
async def register_endpoint(
    body: RegisterRequest,
    reg_svc: RegistrationService = Depends(get_registration_service),
) -> RegisterResponse:
    """Register a new agent client (RFC 7591)."""
    result = await reg_svc.register(body.client_name, body.jwks, scope=body.scope)
    return RegisterResponse(
        client_id=result.client_id,
        registration_access_token=result.registration_access_token,
        registration_client_uri=result.registration_client_uri,
        status=result.status,
        claim_token=result.claim_token,
    )


@router.get("/register/{agent_id}", summary="Poll an agent registration's status")
async def poll_status_endpoint(
    agent_id: Annotated[
        str,
        Path(
            description="ID of the registered agent (its client_id).",
            openapi_examples=ex.example(ex.AGENT_ID),
        ),
    ],
    request: Request,
    reg_svc: RegistrationService = Depends(get_registration_service),
) -> RegistrationStatusResponse:
    """Poll registration status (RFC 7592)."""
    rat = _extract_rat(request)
    result = await reg_svc.poll_status(agent_id, rat)
    return RegistrationStatusResponse(
        client_id=result.client_id,
        status=result.status,
    )


@router.put(
    "/register/{agent_id}",
    summary="Update a client registration (unsupported)",
    responses={
        403: problem(
            "Client update is not supported. This endpoint exists to refuse the RFC 7592 "
            "update call, so it has no success response: every request answers 403 and "
            "nothing is changed. Re-register instead."
        )
    },
)
async def update_registration_endpoint(
    agent_id: Annotated[
        str,
        Path(
            description="ID of the registered agent (its client_id).",
            openapi_examples=ex.example(ex.AGENT_ID),
        ),
    ],
    _identity: Identity = get_current_identity(allow_expired_password=True),
) -> JSONResponse:
    """Client update not supported — returns 403."""
    raise OperationNotSupportedError("client update is not supported")


@router.delete(
    "/register/{agent_id}",
    summary="Delete a client registration (unsupported)",
    responses={
        403: problem(
            "Client deletion is not supported. This endpoint exists to refuse the RFC 7592 "
            "delete call, so it has no success response: every request answers 403 and the "
            "registration remains. Archive the agent instead."
        )
    },
)
async def delete_registration_endpoint(
    agent_id: Annotated[
        str,
        Path(
            description="ID of the registered agent (its client_id).",
            openapi_examples=ex.example(ex.AGENT_ID),
        ),
    ],
    _identity: Identity = get_current_identity(allow_expired_password=True),
) -> JSONResponse:
    """Client deletion not supported — returns 403."""
    raise OperationNotSupportedError("client deletion is not supported")
