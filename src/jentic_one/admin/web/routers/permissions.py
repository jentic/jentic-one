"""Permissions router — catalogue and assignment."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from jentic_one.admin.services.permission_service import PermissionService
from jentic_one.admin.services.user_service import UserService
from jentic_one.admin.web.deps import (
    get_permission_service,
    get_user_service,
)
from jentic_one.admin.web.schemas.permissions import (
    EffectivePermission,
    PermissionListResponse,
    PermissionResponse,
    Permissions,
    SetPermissionsRequest,
)
from jentic_one.admin.web.schemas.users import UserResponse
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web.openapi_responses import conflict, not_found, with_responses

router = APIRouter()


@router.get("/permissions")
async def list_permissions(
    identity: Identity = get_current_identity(),
    perm_svc: PermissionService = Depends(get_permission_service),
) -> PermissionListResponse:
    """List the permission catalogue visible to the caller."""
    entries = await perm_svc.list_catalogue(identity.sub)
    return PermissionListResponse(
        data=[
            PermissionResponse(
                name=e.name,
                description=e.description,
                implies=e.implies,
                grantable_by_caller=e.grantable_by_caller,
            )
            for e in entries
        ]
    )


@router.put(
    "/users/{user_id}/permissions",
    responses=with_responses(
        not_found(),
        conflict("Would remove org:admin from the last active org:admin"),
    ),
)
async def set_user_permissions(
    user_id: str,
    body: SetPermissionsRequest,
    identity: Identity = get_current_identity(required_permissions=["users:write"]),
    perm_svc: PermissionService = Depends(get_permission_service),
    user_svc: UserService = Depends(get_user_service),
) -> UserResponse:
    """Set the assigned permissions for a user."""
    await perm_svc.set_assigned(user_id, body.permissions, identity=identity)
    view = await user_svc.get_by_id(user_id)
    return UserResponse(
        id=view.id,
        email=view.email,
        first_name=view.first_name,
        last_name=view.last_name,
        name=view.name,
        active=view.active,
        auth_provider=view.auth_provider,
        invite_state=view.invite_state,
        must_change_password=view.must_change_password,
        external_subject_id=view.external_subject_id,
        permissions=Permissions(
            assigned=view.assigned,
            effective=[
                EffectivePermission(name=e.name, implied_by=e.implied_by) for e in view.effective
            ],
        ),
        created_at=view.created_at,
        updated_at=view.updated_at,
    )
