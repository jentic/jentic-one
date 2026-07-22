"""Identity router — GET /me endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from jentic.problem_details import Unauthorized

from jentic_one.admin.services.errors import UserNotFoundError
from jentic_one.admin.services.user_service import UserService
from jentic_one.auth.services.agent_service import AgentService
from jentic_one.auth.services.errors import ActorNotFoundError
from jentic_one.auth.services.service_account_service import ServiceAccountService
from jentic_one.auth.web.deps import (
    get_agent_service,
    get_service_account_service,
    get_user_service,
)
from jentic_one.auth.web.schemas.identity import (
    MeAgent,
    MeResponse,
    MeServiceAccount,
    MeUser,
    ToolkitBindingEntry,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorStatus
from jentic_one.shared.web import get_current_identity

router = APIRouter()


@router.get("/me", response_model=MeResponse)
async def get_me(
    request: Request,
    identity: Identity = get_current_identity(allow_expired_password=True),
    user_svc: UserService = Depends(get_user_service),
    agent_svc: AgentService = Depends(get_agent_service),
    sa_svc: ServiceAccountService = Depends(get_service_account_service),
) -> MeUser | MeAgent | MeServiceAccount:
    """Return the caller's identity and context, discriminated by actor type."""
    sub = identity.sub

    if sub.startswith("usr_"):
        return await _resolve_user(request, identity, user_svc)
    elif sub.startswith("agnt_"):
        return await _resolve_agent(request, identity, agent_svc)
    elif sub.startswith("sva_"):
        return await _resolve_service_account(request, identity, sa_svc)
    else:
        raise Unauthorized(
            detail="Unrecognised actor type in token subject",
            instance=request.url.path,
            type="unauthorized",
        )


async def _resolve_user(request: Request, identity: Identity, user_svc: UserService) -> MeUser:
    try:
        user = await user_svc.get_by_id(identity.sub)
    except UserNotFoundError:
        raise Unauthorized(
            detail="User referenced by token no longer exists",
            instance=request.url.path,
            type="unauthorized",
        ) from None
    return MeUser(
        id=user.id,
        name=user.name,
        email=user.email,
        admin="org:admin" in identity.permissions,
        status=ActorStatus.ACTIVE if user.active else ActorStatus.DISABLED,
        scopes=identity.permissions,
        must_change_password=identity.must_change_password,
    )


async def _resolve_agent(request: Request, identity: Identity, agent_svc: AgentService) -> MeAgent:
    try:
        agent = await agent_svc.get_agent(identity.sub, identity=identity)
        toolkits = await agent_svc.list_toolkits(identity.sub, identity=identity)
        # Read the live grants rather than echoing the token's scopes, so an
        # approved grant shows up here immediately even when the presented token
        # was minted before the grant (#673). `token_scopes` exposes the token's
        # own view so the agent can detect (and act on) the staleness gap.
        granted_scopes = await agent_svc.get_scopes(identity.sub, identity=identity)
    except ActorNotFoundError:
        raise Unauthorized(
            detail="Agent referenced by token no longer exists",
            instance=request.url.path,
            type="unauthorized",
        ) from None
    return MeAgent(
        id=agent.id,
        name=agent.name,
        status=agent.status,
        scopes=granted_scopes,
        token_scopes=identity.permissions,
        parent_agent_id=agent.parent_agent_id,
        approved_by=agent.approved_by,
        toolkit_bindings=[
            ToolkitBindingEntry(toolkit_id=tb.toolkit_id, name=tb.name, bound_at=tb.bound_at)
            for tb in toolkits
        ],
    )


async def _resolve_service_account(
    request: Request, identity: Identity, sa_svc: ServiceAccountService
) -> MeServiceAccount:
    try:
        sa = await sa_svc.get_service_account(identity.sub, identity=identity)
        # Mirror the agent path: report live grants as `scopes` and the token's
        # own view as `token_scopes` so a fresh grant shows up immediately and
        # any staleness gap is detectable (#673).
        granted_scopes = await sa_svc.get_scopes(identity.sub, identity=identity)
    except ActorNotFoundError:
        raise Unauthorized(
            detail="Service account referenced by token no longer exists",
            instance=request.url.path,
            type="unauthorized",
        ) from None
    return MeServiceAccount(
        id=sa.id,
        name=sa.name,
        status=sa.status,
        scopes=granted_scopes,
        token_scopes=identity.permissions,
        registered_by=sa.registered_by,
        approved_by=sa.approved_by,
    )
