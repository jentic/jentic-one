"""Agents router — lifecycle CRUD and toolkit bindings."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from jentic.problem_details import Forbidden

from jentic_one.auth.services.agent_auth_service import AgentAuthService
from jentic_one.auth.services.agent_service import AgentService
from jentic_one.auth.services.schemas.agents import (
    AgentCreatePayload,
    AgentView,
    ToolkitBindingView,
)
from jentic_one.auth.web.deps import get_agent_auth_service, get_agent_service
from jentic_one.auth.web.schemas.agents import (
    AgentCreateRequest,
    AgentListResponse,
    AgentPatchRequest,
    AgentResponse,
    AgentScopesRequest,
    AgentScopesResponse,
    ApiKeyHistoryEntryResponse,
    ApiKeyHistoryResponse,
    ApiKeyInfoResponse,
    ApiKeyResponse,
    ClaimRequest,
    DenyRequest,
    ToolkitBindingListResponse,
    ToolkitBindingResponse,
    ToolkitBindRequest,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.web import get_current_identity

router = APIRouter()


def _agent_response(view: AgentView) -> AgentResponse:
    return AgentResponse(
        id=view.id,
        name=view.name,
        description=view.description,
        owner_id=view.owner_id,
        registered_by=view.registered_by,
        parent_agent_id=view.parent_agent_id,
        approved_by=view.approved_by,
        status=view.status,
        denial_reason=view.denial_reason,
        denied_by=view.denied_by,
        created_at=view.created_at,
        approved_at=view.approved_at,
        has_api_key=view.has_api_key,
    )


def _toolkit_response(view: ToolkitBindingView) -> ToolkitBindingResponse:
    return ToolkitBindingResponse(
        id=view.id,
        agent_id=view.agent_id,
        toolkit_id=view.toolkit_id,
        bound_at=view.bound_at,
    )


@router.post("/agents", status_code=201)
async def create_agent(
    body: AgentCreateRequest,
    identity: Identity = get_current_identity(required_permissions=["agents:write"]),
    agent_svc: AgentService = Depends(get_agent_service),
) -> AgentResponse:
    """Create a new agent manually."""
    view = await agent_svc.create(
        AgentCreatePayload(name=body.name, description=body.description, scopes=body.scopes),
        owner_id=identity.sub,
        identity=identity,
    )
    return _agent_response(view)


@router.get("/agents")
async def list_agents(
    identity: Identity = get_current_identity(required_permissions=["agents:read"]),
    agent_svc: AgentService = Depends(get_agent_service),
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    status: str | None = Query(default=None),
) -> AgentListResponse:
    """List agents — scoped by identity via dynamic query scoping."""
    page = await agent_svc.list_agents(limit=limit, status=status, cursor=cursor, identity=identity)
    return AgentListResponse(
        data=[_agent_response(a) for a in page.data],
        has_more=page.has_more,
        next_cursor=page.next_cursor,
    )


@router.get("/agents/{agent_id}")
async def get_agent(
    agent_id: str,
    request: Request,
    identity: Identity = get_current_identity(allow_expired_password=True),
    agent_svc: AgentService = Depends(get_agent_service),
) -> AgentResponse:
    """Get agent by ID — requires agents:read or self-read."""
    view = await agent_svc.get_agent(agent_id, identity=identity)
    _check_read_access(identity, view, request)
    return _agent_response(view)


@router.patch("/agents/{agent_id}", status_code=200)
async def update_agent(
    agent_id: str,
    body: AgentPatchRequest,
    identity: Identity = get_current_identity(required_permissions=["agents:write"]),
    agent_svc: AgentService = Depends(get_agent_service),
) -> AgentResponse:
    """Partially update an agent — name, description, or owner_id."""
    update_data = body.model_dump(exclude_unset=True)
    if not update_data:
        view = await agent_svc.get_agent(agent_id, identity=identity)
        return _agent_response(view)
    view = await agent_svc.update_agent(agent_id, update_data=update_data, identity=identity)
    return _agent_response(view)


@router.post("/agents/{agent_id}:approve", status_code=200)
async def approve_agent(
    agent_id: str,
    identity: Identity = get_current_identity(required_permissions=["agents:write"]),
    agent_svc: AgentService = Depends(get_agent_service),
) -> AgentResponse:
    """Approve a pending agent."""
    view = await agent_svc.approve(agent_id, identity=identity)
    return _agent_response(view)


@router.post("/agents/{agent_id}:claim", status_code=200)
async def claim_agent(
    agent_id: str,
    body: ClaimRequest,
    identity: Identity = get_current_identity(allow_expired_password=True),
    agent_svc: AgentService = Depends(get_agent_service),
) -> AgentResponse:
    """Claim ownership of a self-registered agent using its claim token.

    Authenticated by the platform bearer token but requires **no** agent
    permission — the single-use claim token minted at ``/register`` is the proof,
    so the registering human (even a plain member) can take ownership. Sets
    ``owner_id`` to the caller; the existing scoping + approve paths then apply.
    """
    view = await agent_svc.claim(agent_id, token=body.token, identity=identity)
    return _agent_response(view)


@router.post("/agents/{agent_id}:deny", status_code=200)
async def deny_agent(
    agent_id: str,
    body: DenyRequest,
    identity: Identity = get_current_identity(required_permissions=["agents:write"]),
    agent_svc: AgentService = Depends(get_agent_service),
) -> AgentResponse:
    """Deny a pending agent."""
    view = await agent_svc.deny(agent_id, reason=body.reason, identity=identity)
    return _agent_response(view)


@router.post("/agents/{agent_id}:disable", status_code=204)
async def disable_agent(
    agent_id: str,
    identity: Identity = get_current_identity(required_permissions=["agents:write"]),
    agent_svc: AgentService = Depends(get_agent_service),
) -> Response:
    """Disable an active agent."""
    await agent_svc.disable(agent_id, identity=identity)
    return Response(status_code=204)


@router.post("/agents/{agent_id}:enable", status_code=204)
async def enable_agent(
    agent_id: str,
    identity: Identity = get_current_identity(required_permissions=["agents:write"]),
    agent_svc: AgentService = Depends(get_agent_service),
) -> Response:
    """Enable a disabled agent."""
    await agent_svc.enable(agent_id, identity=identity)
    return Response(status_code=204)


@router.delete("/agents/{agent_id}", status_code=204)
async def archive_agent(
    agent_id: str,
    identity: Identity = get_current_identity(required_permissions=["agents:write"]),
    agent_svc: AgentService = Depends(get_agent_service),
) -> Response:
    """Soft-archive an agent — revokes scope grants and toolkit bindings."""
    await agent_svc.archive(agent_id, identity=identity)
    return Response(status_code=204)


@router.get("/agents/{agent_id}/scopes", operation_id="getAgentScopes")
async def get_agent_scopes(
    agent_id: str,
    identity: Identity = get_current_identity(required_permissions=["agents:read"]),
    agent_svc: AgentService = Depends(get_agent_service),
) -> AgentScopesResponse:
    """List scopes granted to an agent."""
    scopes = await agent_svc.get_scopes(agent_id, identity=identity)
    return AgentScopesResponse(scopes=scopes)


@router.put("/agents/{agent_id}/scopes", operation_id="replaceAgentScopes")
async def replace_agent_scopes(
    agent_id: str,
    body: AgentScopesRequest,
    identity: Identity = get_current_identity(required_permissions=["agents:write"]),
    agent_svc: AgentService = Depends(get_agent_service),
) -> AgentScopesResponse:
    """Replace all scopes for an agent."""
    scopes = await agent_svc.replace_scopes(agent_id, body.scopes, identity=identity)
    return AgentScopesResponse(scopes=scopes)


@router.get("/agents/{agent_id}/toolkits", operation_id="listAgentToolkits")
async def list_toolkits(
    agent_id: str,
    request: Request,
    identity: Identity = get_current_identity(allow_expired_password=True),
    agent_svc: AgentService = Depends(get_agent_service),
) -> ToolkitBindingListResponse:
    """List toolkit bindings for an agent — requires agents:read or self."""
    view = await agent_svc.get_agent(agent_id, identity=identity)
    _check_read_access(identity, view, request)
    bindings = await agent_svc.list_toolkits(agent_id, identity=identity)
    return ToolkitBindingListResponse(data=[_toolkit_response(b) for b in bindings])


@router.post("/agents/{agent_id}/toolkits", status_code=201)
async def bind_toolkit(
    agent_id: str,
    body: ToolkitBindRequest,
    identity: Identity = get_current_identity(required_permissions=["agents:write"]),
    agent_svc: AgentService = Depends(get_agent_service),
) -> ToolkitBindingResponse:
    """Bind a toolkit to an agent."""
    binding = await agent_svc.bind_toolkit(agent_id, toolkit_id=body.toolkit_id, identity=identity)
    return _toolkit_response(binding)


@router.delete("/agents/{agent_id}/toolkits/{toolkit_id}", status_code=204)
async def unbind_toolkit(
    agent_id: str,
    toolkit_id: str,
    identity: Identity = get_current_identity(required_permissions=["agents:write"]),
    agent_svc: AgentService = Depends(get_agent_service),
) -> Response:
    """Unbind a toolkit from an agent."""
    await agent_svc.unbind_toolkit(agent_id, toolkit_id=toolkit_id, identity=identity)
    return Response(status_code=204)


@router.post("/agents/{agent_id}:generate-api-key", status_code=200)
async def generate_agent_api_key(
    agent_id: str,
    identity: Identity = get_current_identity(required_permissions=["agents:write"]),
    auth_svc: AgentAuthService = Depends(get_agent_auth_service),
) -> ApiKeyResponse:
    """Generate a new API key for an active agent. Rotates any existing key."""
    key = await auth_svc.register_api_key(agent_id, identity=identity)
    return ApiKeyResponse(key=key)


@router.post("/agents/{agent_id}:revoke-api-key", status_code=204)
async def revoke_agent_api_key(
    agent_id: str,
    identity: Identity = get_current_identity(required_permissions=["agents:write"]),
    auth_svc: AgentAuthService = Depends(get_agent_auth_service),
) -> Response:
    """Revoke an agent's API key without generating a new one."""
    await auth_svc.revoke_api_key(agent_id, identity=identity)
    return Response(status_code=204)


@router.get("/agents/{agent_id}/api-key", operation_id="getAgentApiKeyInfo")
async def get_agent_api_key_info(
    agent_id: str,
    identity: Identity = get_current_identity(required_permissions=["agents:read"]),
    auth_svc: AgentAuthService = Depends(get_agent_auth_service),
) -> ApiKeyInfoResponse | None:
    """Get API key metadata for an agent. Returns info even after revocation."""
    info = await auth_svc.get_api_key_info(agent_id, identity=identity)
    if info is None:
        return None
    return ApiKeyInfoResponse(
        id=info.id,
        status=info.status,
        created_at=info.created_at,
        rotated_at=info.rotated_at,
        created_by=info.created_by,
    )


@router.get("/agents/{agent_id}/api-key/history", operation_id="getAgentApiKeyHistory")
async def get_agent_api_key_history(
    agent_id: str,
    identity: Identity = get_current_identity(required_permissions=["agents:read"]),
    auth_svc: AgentAuthService = Depends(get_agent_auth_service),
) -> ApiKeyHistoryResponse:
    """Get the audit history of API key operations for an agent."""
    entries = await auth_svc.get_api_key_history(agent_id, identity=identity)
    return ApiKeyHistoryResponse(
        data=[
            ApiKeyHistoryEntryResponse(
                id=e.id,
                action=e.action,
                reason=e.reason,
                actor_id=e.actor_id,
                occurred_at=e.occurred_at,
            )
            for e in entries
        ]
    )


def _check_read_access(identity: Identity, view: AgentView, request: Request) -> None:
    """Allow if caller has agents:read, is org:admin, or is the agent itself."""
    caller_perms = set(identity.permissions)
    if "org:admin" in caller_perms or "agents:read" in caller_perms:
        return
    if identity.sub == view.id or identity.sub == view.owner_id:
        return
    raise Forbidden(
        detail="You do not have access to this agent",
        instance=request.url.path,
        type="forbidden",
    )
