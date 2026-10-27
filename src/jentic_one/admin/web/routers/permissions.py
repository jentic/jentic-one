"""Permissions router — catalogue and assignment."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path

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
from jentic_one.shared.web import openapi_examples as ex
from jentic_one.shared.web.openapi_responses import conflict, not_found, with_responses

router = APIRouter()


@router.get("/permissions", summary="List the permission catalogue")
async def list_permissions(
    identity: Identity = get_current_identity(),
    perm_svc: PermissionService = Depends(get_permission_service),
) -> PermissionListResponse:
    """List every permission, flagged with whether the caller may grant it to an agent.

    Each entry gives the permission `name`, a `description`, the permissions
    it `implies`, and `grantable_by_caller` — whether `POST /agents` and
    `PUT /agents/{agent_id}/permissions` would accept it from this caller.
    `org:admin` is listed only to callers who hold it. Use it to build an
    agent permission picker. Any signed-in caller may call it.
    """
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
        conflict(
            "Would remove org:admin from the last active org:admin, or the user has been deleted"
        ),
    ),
)
async def set_user_permissions(
    user_id: Annotated[
        str, Path(description="ID of the user.", openapi_examples=ex.example(ex.USER_ID))
    ],
    body: SetPermissionsRequest,
    identity: Identity = get_current_identity(required_permissions=["users:write"]),
    perm_svc: PermissionService = Depends(get_permission_service),
    user_svc: UserService = Depends(get_user_service),
) -> UserResponse:
    """Set the assigned permissions for a user.

    A deleted user's permissions cannot be changed (409). When the new set
    removes any of the user's effective permissions, their login sessions
    end: every login JWT issued before the change is refused (401), and the
    user signs in again for a token with the narrower set. This includes a
    caller narrowing their own permissions.
    """
    await perm_svc.set_assigned(user_id, body.permissions, identity=identity)
    view = await user_svc.get(user_id, identity=identity)
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
