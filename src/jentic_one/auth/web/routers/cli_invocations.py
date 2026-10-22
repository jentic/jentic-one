"""CLI invocations router — mint, renew and revoke vendor CLI invocation tokens.

Served by the auth surface (it owns the admin-DB token writes) under a
``/cli-invocations`` prefix of its own, so the split-deploy gateway can route
it to the auth backend without claiming any other surface's paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, Response

from jentic_one.auth.services.cli_invocation_service import CliInvocationService
from jentic_one.auth.services.errors import RateLimitExceededError
from jentic_one.auth.services.schemas.cli_invocations import (
    CliInvocationMintPayload,
    CliInvocationView,
)
from jentic_one.auth.web.deps import get_cli_invocation_service
from jentic_one.auth.web.ratelimit import get_auth_backend
from jentic_one.auth.web.schemas.cli_invocations import (
    CliInvocationCreatedResponse,
    CliInvocationCreateRequest,
    CliInvocationResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import BROKER_EXECUTE_PERMISSION
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from jentic_one.shared.resilience import RateLimiter
from jentic_one.shared.web import get_ctx, get_current_identity
from jentic_one.shared.web import openapi_examples as ex
from jentic_one.shared.web.auth import extract_credential
from jentic_one.shared.web.openapi_responses import (
    conflict,
    not_found,
    too_many_requests,
    with_responses,
)

router = APIRouter()

CliInvocationIdPath = Annotated[
    str,
    Path(
        description="ID of the CLI invocation.", openapi_examples=ex.example(ex.CLI_INVOCATION_ID)
    ),
]


@dataclass(frozen=True, slots=True)
class _Minter:
    """The minting agent and the raw credential it authenticated this request with."""

    identity: Identity
    credential: str


def _get_mint_limiter(request: Request, ctx: Context) -> RateLimiter:
    limiter: RateLimiter | None = getattr(request.app.state, "_cli_invocation_mint_limiter", None)
    if limiter is not None:
        return limiter
    rpm = ctx.config.cli_facades.mint_rate_limit_per_minute
    # Own bucket namespace, keyed on the authenticated agent id: the plain-git
    # credential helper mints with no exit hook, so editors auto-fetching
    # inside ``jentic run`` must not be able to flood the table.
    limiter = RateLimiter(
        get_auth_backend(request), default_rpm=rpm, burst=rpm, namespace="cli-invocation-mint"
    )
    request.app.state._cli_invocation_mint_limiter = limiter
    return limiter


async def require_cli_invocation_minter(
    request: Request,
    identity: Identity = get_current_identity(
        required_permissions=[BROKER_EXECUTE_PERMISSION], require_actor_type=ActorType.AGENT
    ),
    ctx: Context = Depends(get_ctx),
) -> _Minter:
    """Authenticate an executing agent, apply its mint rate limit, keep its credential.

    The credential is handed to the service, which accepts only the agent's
    own ``at_`` access token as the invocation token's parent.
    """
    outcome = await _get_mint_limiter(request, ctx).acquire(identity.sub)
    if not outcome.allowed:
        raise RateLimitExceededError(retry_after=outcome.retry_after_s)
    return _Minter(identity=identity, credential=extract_credential(request))


def _invocation_response(view: CliInvocationView) -> CliInvocationResponse:
    return CliInvocationResponse(
        id=view.id,
        vendor=view.vendor,
        credential_name=view.credential_name,
        trace_id=view.trace_id,
        command_title=view.command_title,
        expires_at=view.expires_at,
        hard_expires_at=view.hard_expires_at,
        revoked_at=view.revoked_at,
    )


@router.post(
    "/cli-invocations",
    status_code=201,
    summary="Mint a CLI invocation token",
    responses=with_responses(
        not_found("No CLI facade is available for `vendor` (`cli_facade_not_found`)."),
        too_many_requests("The agent's mint rate limit is exhausted; honour `Retry-After`."),
    ),
)
async def create_cli_invocation(
    body: CliInvocationCreateRequest,
    minter: _Minter = Depends(require_cli_invocation_minter),
    svc: CliInvocationService = Depends(get_cli_invocation_service),
) -> CliInvocationCreatedResponse:
    """Mint a short-lived invocation token for one vendor CLI command.

    Called by `jentic gh`/`cf`/`aws` before it runs the vendor CLI. Only an
    agent holding `capabilities:execute` and authenticated with its own
    access token (`at_…`, not an API key or JWT) may mint; anything else
    answers 403 (`cli_invocation_mint_forbidden`). The token lives
    `cli_facades.invocation_ttl_seconds` (renew it with `:renew`), never past
    `hard_expires_at`, and never past the access token it was minted with:
    revoking or expiring that access token ends the invocation token too.
    It is accepted only by the Broker's CLI facade for `vendor`; `/execute`,
    the control plane and MCP reject it. Answers 404 while
    `cli_facades.enabled` or the vendor's facade is off. Minting is rate
    limited per agent (`cli_facades.mint_rate_limit_per_minute`).
    """
    minted = await svc.mint(
        CliInvocationMintPayload(
            vendor=body.vendor,
            credential_name=body.credential_name,
            command_title=body.command_title,
        ),
        identity=minter.identity,
        parent_token=minter.credential,
    )
    view = _invocation_response(minted.invocation)
    return CliInvocationCreatedResponse(**view.model_dump(), token=minted.token)


@router.post(
    "/cli-invocations/{invocation_id}:renew",
    summary="Renew a CLI invocation token",
    responses=with_responses(
        not_found("The invocation does not exist or is not the caller's."),
        conflict("The invocation is revoked or expired (`cli_invocation_not_renewable`)."),
    ),
)
async def renew_cli_invocation(
    invocation_id: CliInvocationIdPath,
    identity: Identity = get_current_identity(require_actor_type=ActorType.AGENT),
    svc: CliInvocationService = Depends(get_cli_invocation_service),
) -> CliInvocationResponse:
    """Extend one of the caller's live invocation tokens.

    Moves `expires_at` to `cli_facades.invocation_ttl_seconds` from now, capped at
    `hard_expires_at`. The CLI wrapper calls this on a heartbeat while the
    command runs. Only the agent that minted the invocation may renew it;
    any other caller gets 404. A revoked or expired invocation answers 409.
    """
    view = await svc.renew(invocation_id, identity=identity)
    return _invocation_response(view)


@router.post(
    "/cli-invocations/{invocation_id}:revoke",
    status_code=204,
    summary="Revoke a CLI invocation token",
    responses=not_found("The invocation does not exist or is not the caller's."),
)
async def revoke_cli_invocation(
    invocation_id: CliInvocationIdPath,
    identity: Identity = get_current_identity(require_actor_type=ActorType.AGENT),
    svc: CliInvocationService = Depends(get_cli_invocation_service),
) -> Response:
    """Revoke one of the caller's invocation tokens.

    The CLI wrapper calls this when the command exits. The Broker stops
    accepting the token within its token-cache TTL
    (`broker.resolve_cache_ttl_seconds`). Only the agent that minted the
    invocation may revoke it; any other caller gets 404. Revoking an
    already-revoked invocation succeeds.
    """
    await svc.revoke(invocation_id, identity=identity)
    return Response(status_code=204)
