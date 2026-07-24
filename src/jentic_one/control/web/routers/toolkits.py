"""Toolkits router — CRUD for toolkit management."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from pydantic import Field

from jentic_one.control.core.schema.toolkit_credential_bindings import ToolkitCredentialBinding
from jentic_one.control.core.schema.toolkit_keys import ToolkitKey
from jentic_one.control.core.schema.toolkit_permission_rules import ToolkitPermissionRule
from jentic_one.control.core.schema.toolkits import Toolkit
from jentic_one.control.services.toolkits.service import ToolkitService
from jentic_one.control.web.deps import get_toolkit_service
from jentic_one.control.web.schemas.toolkits import (
    PermissionRuleListResponse,
    PermissionRuleReadSchema,
    PermissionRuleSchema,
    PermissionsPatchRequest,
    ToolkitAgentListResponse,
    ToolkitAgentResponse,
    ToolkitCreateRequest,
    ToolkitCreateResponse,
    ToolkitCredentialBindingResponse,
    ToolkitCredentialBindRequest,
    ToolkitCredentialListResponse,
    ToolkitKeyCreateRequest,
    ToolkitKeyCreateResponse,
    ToolkitKeyListResponse,
    ToolkitKeyResponse,
    ToolkitKeyUpdateRequest,
    ToolkitListResponse,
    ToolkitResponse,
    ToolkitUpdateRequest,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web.openapi_responses import conflict, not_found, with_responses

router = APIRouter()


def _to_toolkit_response(toolkit: Toolkit) -> ToolkitResponse:
    return ToolkitResponse(
        toolkit_id=toolkit.id,
        name=toolkit.name,
        description=toolkit.description,
        active=toolkit.active,
        permissions=toolkit.permissions,
        key_count=len(toolkit.keys),
        credential_count=len(toolkit.bindings),
        created_by=toolkit.created_by,
        created_at=toolkit.created_at,
        updated_at=toolkit.updated_at,
    )


def _to_key_response(key: ToolkitKey) -> ToolkitKeyResponse:
    return ToolkitKeyResponse(
        key_id=key.id,
        toolkit_id=key.toolkit_id,
        label=key.label,
        allowed_ips=key.allowed_ips,
        revoked=key.revoked,
        created_at=key.created_at,
        last_used_at=key.last_used_at,
        key_preview=key.key_preview,
    )


def _to_binding_response(
    binding: ToolkitCredentialBinding,
    permissions: list[ToolkitPermissionRule] | None = None,
) -> ToolkitCredentialBindingResponse:
    cred = binding.credential
    return ToolkitCredentialBindingResponse(
        toolkit_id=binding.toolkit_id,
        credential_id=binding.credential_id,
        api_vendor=cred.api_vendor if cred else None,
        api_name=cred.api_name if cred else None,
        credential_type=cred.type if cred else None,
        label=cred.name if cred else None,
        bound_at=binding.bound_at,
        permissions=[_to_permission_rule(r) for r in permissions] if permissions else [],
    )


def _to_permission_rule(rule: ToolkitPermissionRule) -> PermissionRuleReadSchema:
    return PermissionRuleReadSchema.model_validate(
        {
            "effect": rule.effect,
            "methods": rule.methods,
            "path": rule.path,
            "operations": rule.operations,
            "_system": rule.is_system,
            "_comment": rule.comment,
        }
    )


# --- Toolkit CRUD ---


@router.post("/toolkits", status_code=201, summary="Create toolkit")
async def create_toolkit(
    body: ToolkitCreateRequest,
    identity: Identity = get_current_identity(required_permissions=["toolkits:write"]),
    svc: ToolkitService = Depends(get_toolkit_service),
) -> ToolkitCreateResponse:
    """Create a toolkit and issue its first API key.

    The plaintext key (`jntc_live_…`) is returned **once** in `api_key` and is
    never retrievable again. Optional `credential_ids` bind existing credentials
    at creation time.
    """
    permissions = (
        [p.model_dump(exclude_none=True) for p in body.permissions] if body.permissions else None
    )
    toolkit, plaintext = await svc.create(
        name=body.name,
        identity=identity,
        description=body.description,
        active=body.active,
        permissions=permissions,
        credential_ids=body.credential_ids,
    )
    return ToolkitCreateResponse(
        toolkit=_to_toolkit_response(toolkit),
        api_key=plaintext,
    )


@router.get("/toolkits", summary="List toolkits")
async def list_toolkits(
    identity: Identity = get_current_identity(
        required_permissions=["toolkits:read", "owner:toolkits:read"]
    ),
    svc: ToolkitService = Depends(get_toolkit_service),
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> ToolkitListResponse:
    """List toolkits with cursor-based pagination."""
    data, has_more, next_cursor = await svc.list_all(cursor=cursor, limit=limit, identity=identity)
    return ToolkitListResponse(
        data=[_to_toolkit_response(t) for t in data],
        has_more=has_more,
        next_cursor=next_cursor,
    )


@router.get("/toolkits/{toolkit_id}", summary="Get toolkit", responses=not_found())
async def get_toolkit(
    toolkit_id: str,
    identity: Identity = get_current_identity(
        required_permissions=["toolkits:read", "owner:toolkits:read"]
    ),
    svc: ToolkitService = Depends(get_toolkit_service),
) -> ToolkitResponse:
    """Get a single toolkit by its `tk_…` ID."""
    toolkit = await svc.get(toolkit_id, identity=identity)
    return _to_toolkit_response(toolkit)


@router.patch("/toolkits/{toolkit_id}", summary="Update toolkit", responses=not_found())
async def update_toolkit(
    toolkit_id: str,
    body: ToolkitUpdateRequest,
    identity: Identity = get_current_identity(required_permissions=["toolkits:write"]),
    svc: ToolkitService = Depends(get_toolkit_service),
) -> ToolkitResponse:
    """Update a toolkit's name, description, or active flag."""
    toolkit = await svc.update(
        toolkit_id,
        identity=identity,
        name=body.name,
        description=body.description,
        active=body.active,
    )
    return _to_toolkit_response(toolkit)


@router.delete(
    "/toolkits/{toolkit_id}",
    status_code=204,
    summary="Delete toolkit",
    responses=not_found(),
)
async def delete_toolkit(
    toolkit_id: str,
    identity: Identity = get_current_identity(required_permissions=["toolkits:write"]),
    svc: ToolkitService = Depends(get_toolkit_service),
) -> Response:
    """Permanently delete a toolkit and cascade-remove its keys, bindings, and permission rules."""
    await svc.delete(toolkit_id, identity=identity)
    return Response(status_code=204)


# --- Agent bindings (reverse lookup) ---


@router.get(
    "/toolkits/{toolkit_id}/agents",
    summary="List agents bound to toolkit",
    responses=not_found(),
)
async def list_toolkit_agents(
    toolkit_id: str,
    identity: Identity = get_current_identity(
        required_permissions=["toolkits:read", "owner:toolkits:read"]
    ),
    svc: ToolkitService = Depends(get_toolkit_service),
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> ToolkitAgentListResponse:
    """List agents bound to a toolkit with cursor-based pagination."""
    data, has_more, next_cursor = await svc.list_agents(
        toolkit_id, cursor=cursor, limit=limit, identity=identity
    )
    return ToolkitAgentListResponse(
        data=[
            ToolkitAgentResponse(
                agent_id=row.agent_id,
                agent_name=row.agent_name,
                status=row.agent_status,
                bound_at=row.bound_at,
            )
            for row in data
        ],
        has_more=has_more,
        next_cursor=next_cursor,
    )


# --- Key management ---


@router.post(
    "/toolkits/{toolkit_id}/keys",
    status_code=201,
    summary="Issue toolkit key",
    responses=not_found(),
)
async def create_key(
    toolkit_id: str,
    body: ToolkitKeyCreateRequest,
    identity: Identity = get_current_identity(required_permissions=["toolkits:write"]),
    svc: ToolkitService = Depends(get_toolkit_service),
) -> ToolkitKeyCreateResponse:
    """Issue a new API key for a toolkit.

    The plaintext value (`jntc_live_…`) is returned **once** in `api_key`. Issue
    a fresh key, switch callers, then revoke the old one (do-and-then-revoke).
    """
    key, plaintext = await svc.create_key(
        toolkit_id, identity=identity, label=body.label, allowed_ips=body.allowed_ips
    )
    return ToolkitKeyCreateResponse(
        key=_to_key_response(key),
        api_key=plaintext,
    )


@router.get("/toolkits/{toolkit_id}/keys", summary="List toolkit keys", responses=not_found())
async def list_keys(
    toolkit_id: str,
    identity: Identity = get_current_identity(
        required_permissions=["toolkits:read", "owner:toolkits:read"]
    ),
    svc: ToolkitService = Depends(get_toolkit_service),
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> ToolkitKeyListResponse:
    """List a toolkit's API keys (redacted; only `key_preview` is shown)."""
    data, has_more, next_cursor = await svc.list_keys(
        toolkit_id, cursor=cursor, limit=limit, identity=identity
    )
    return ToolkitKeyListResponse(
        data=[_to_key_response(k) for k in data],
        has_more=has_more,
        next_cursor=next_cursor,
    )


@router.patch(
    "/toolkits/{toolkit_id}/keys/{key_id}",
    summary="Update toolkit key",
    responses=not_found(),
)
async def update_key(
    toolkit_id: str,
    key_id: str,
    body: ToolkitKeyUpdateRequest,
    identity: Identity = get_current_identity(required_permissions=["toolkits:write"]),
    svc: ToolkitService = Depends(get_toolkit_service),
) -> ToolkitKeyResponse:
    """Update a key's label, IP allowlist, or revoked flag."""
    key = await svc.update_key(
        toolkit_id,
        key_id,
        identity=identity,
        label=body.label,
        allowed_ips=body.allowed_ips,
        revoked=body.revoked,
    )
    return _to_key_response(key)


@router.delete(
    "/toolkits/{toolkit_id}/keys/{key_id}",
    status_code=204,
    summary="Revoke toolkit key",
    responses=not_found(),
)
async def delete_key(
    toolkit_id: str,
    key_id: str,
    identity: Identity = get_current_identity(required_permissions=["toolkits:write"]),
    svc: ToolkitService = Depends(get_toolkit_service),
) -> Response:
    """Revoke (delete) a toolkit API key. Callers using it are rejected immediately."""
    await svc.delete_key(toolkit_id, key_id, identity=identity)
    return Response(status_code=204)


# --- Credential bindings ---


@router.post(
    "/toolkits/{toolkit_id}/credentials",
    status_code=201,
    summary="Bind credential to toolkit",
    responses=with_responses(not_found(), conflict("Credential already bound")),
)
async def bind_credential(
    toolkit_id: str,
    body: ToolkitCredentialBindRequest,
    identity: Identity = get_current_identity(required_permissions=["toolkits:write"]),
    svc: ToolkitService = Depends(get_toolkit_service),
) -> ToolkitCredentialBindingResponse:
    """Bind an existing credential to a toolkit, optionally with inline permission rules."""
    permissions_data = None
    if body.permissions:
        permissions_data = [p.model_dump(exclude_none=True) for p in body.permissions]
    binding = await svc.bind_credential(
        toolkit_id, body.credential_id, identity=identity, permissions=permissions_data
    )
    rules = await svc.list_permissions(toolkit_id, body.credential_id, identity=identity)
    return _to_binding_response(binding, rules)


@router.get(
    "/toolkits/{toolkit_id}/credentials",
    summary="List toolkit credential bindings",
    responses=not_found(),
)
async def list_bindings(
    toolkit_id: str,
    identity: Identity = get_current_identity(
        required_permissions=["toolkits:read", "owner:toolkits:read"]
    ),
    svc: ToolkitService = Depends(get_toolkit_service),
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> ToolkitCredentialListResponse:
    """List the credentials bound to a toolkit with cursor-based pagination."""
    page = await svc.list_bindings(toolkit_id, cursor=cursor, limit=limit, identity=identity)
    return ToolkitCredentialListResponse(
        data=[_to_binding_response(item.binding, item.rules) for item in page.data],
        has_more=page.has_more,
        next_cursor=page.next_cursor,
    )


@router.delete(
    "/toolkits/{toolkit_id}/credentials/{credential_id}",
    status_code=204,
    summary="Unbind credential from toolkit",
    responses=not_found(),
)
async def unbind_credential(
    toolkit_id: str,
    credential_id: str,
    identity: Identity = get_current_identity(required_permissions=["toolkits:write"]),
    svc: ToolkitService = Depends(get_toolkit_service),
) -> Response:
    """Remove a credential binding from a toolkit (the credential itself is untouched)."""
    await svc.unbind_credential(toolkit_id, credential_id, identity=identity)
    return Response(status_code=204)


# --- Permission rules ---


@router.get(
    "/toolkits/{toolkit_id}/credentials/{credential_id}/permissions",
    operation_id="listToolkitPermissions",
    summary="List binding permission rules",
    responses=not_found(),
)
async def list_permissions(
    toolkit_id: str,
    credential_id: str,
    identity: Identity = get_current_identity(
        required_permissions=["toolkits:read", "owner:toolkits:read"]
    ),
    svc: ToolkitService = Depends(get_toolkit_service),
) -> PermissionRuleListResponse:
    """List the fine-grained PBAC rules for a `(toolkit, credential)` binding."""
    rules = await svc.list_permissions(toolkit_id, credential_id, identity=identity)
    return PermissionRuleListResponse(data=[_to_permission_rule(r) for r in rules])


@router.put(
    "/toolkits/{toolkit_id}/credentials/{credential_id}/permissions",
    summary="Replace binding permission rules",
    responses=not_found(),
)
async def replace_permissions(
    toolkit_id: str,
    credential_id: str,
    body: Annotated[list[PermissionRuleSchema], Field(max_length=100)],
    identity: Identity = get_current_identity(required_permissions=["toolkits:write"]),
    svc: ToolkitService = Depends(get_toolkit_service),
) -> PermissionRuleListResponse:
    """Replace the full set of permission rules for a binding (idempotent PUT)."""
    rules_data = [r.model_dump(exclude_none=True) for r in body]
    rules = await svc.replace_permissions(toolkit_id, credential_id, rules_data, identity=identity)
    return PermissionRuleListResponse(data=[_to_permission_rule(r) for r in rules])


@router.patch(
    "/toolkits/{toolkit_id}/credentials/{credential_id}/permissions",
    summary="Patch binding permission rules",
    responses=not_found(),
)
async def patch_permissions(
    toolkit_id: str,
    credential_id: str,
    body: PermissionsPatchRequest,
    identity: Identity = get_current_identity(required_permissions=["toolkits:write"]),
    svc: ToolkitService = Depends(get_toolkit_service),
) -> PermissionRuleListResponse:
    """Additively add and/or remove permission rules on a binding."""
    add_data = None
    if body.add:
        add_data = [r.model_dump(exclude_none=True) for r in body.add]
    rules = await svc.patch_permissions(
        toolkit_id, credential_id, identity=identity, add=add_data, remove=body.remove
    )
    return PermissionRuleListResponse(data=[_to_permission_rule(r) for r in rules])
