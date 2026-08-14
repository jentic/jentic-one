"""Dynamic Client Registration endpoints (RFC 7591/7592 subset)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from jentic_one.auth.services.errors import (
    OperationNotSupportedError,
    RegistrationAccessDeniedError,
)
from jentic_one.auth.services.registration_service import RegistrationService
from jentic_one.auth.web.schemas.registration import (
    RegisterRequest,
    RegisterResponse,
    RegistrationStatusResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web.deps import get_ctx

router = APIRouter()


def get_registration_service(ctx: Context = Depends(get_ctx)) -> RegistrationService:
    return RegistrationService(ctx)


def _extract_rat(request: Request) -> str:
    """Extract Registration Access Token from Authorization header."""
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise RegistrationAccessDeniedError("missing or invalid authorization header")
    return auth[7:]


@router.post("/register", status_code=201)
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


@router.get("/register/{agent_id}")
async def poll_status_endpoint(
    agent_id: str,
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


@router.put("/register/{agent_id}")
async def update_registration_endpoint(
    agent_id: str,
    _identity: Identity = get_current_identity(allow_expired_password=True),
) -> JSONResponse:
    """Client update not supported — returns 403."""
    raise OperationNotSupportedError("client update is not supported")


@router.delete("/register/{agent_id}")
async def delete_registration_endpoint(
    agent_id: str,
    _identity: Identity = get_current_identity(allow_expired_password=True),
) -> JSONResponse:
    """Client deletion not supported — returns 403."""
    raise OperationNotSupportedError("client deletion is not supported")
