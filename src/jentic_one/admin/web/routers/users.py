"""Users router — admin-managed CRUD and action verbs."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response

from jentic_one.admin.services.schemas.users import (
    UserCreatePayload,
    UserUpdatePayload,
    UserView,
)
from jentic_one.admin.services.user_service import UserService
from jentic_one.admin.web.deps import get_user_service
from jentic_one.admin.web.schemas.permissions import EffectivePermission, Permissions
from jentic_one.admin.web.schemas.users import (
    InviteIssuedResponse,
    UserCreatedResponse,
    UserCreateRequest,
    UserListResponse,
    UserResponse,
    UserUpdateRequest,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import InviteState
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web import openapi_examples as ex
from jentic_one.shared.web.openapi_responses import conflict, not_found, with_responses

router = APIRouter()

_LAST_ADMIN = "The user is the last active org:admin"


def _user_response(view: UserView) -> UserResponse:
    """Project a UserView to a UserResponse."""
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


@router.get("/users")
async def list_users(
    identity: Identity = get_current_identity(required_permissions=["users:read"]),
    user_svc: UserService = Depends(get_user_service),
    cursor: str | None = Query(
        default=None,
        description="Opaque cursor from a previous page's `next_cursor`; omit for the first page.",
        openapi_examples=ex.example(ex.CURSOR),
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of users to return (1-200, default 50).",
        openapi_examples=ex.example(25),
    ),
    invite_state: InviteState | None = Query(
        default=None,
        description=(
            "Only return users in this invite state. `expired` matches pending users whose "
            "invite has lapsed; `pending` matches only those with a live invite."
        ),
        openapi_examples=ex.example("pending"),
    ),
) -> UserListResponse:
    """List users with cursor-based pagination.

    Scoped to the caller: org admins see every user; anyone else sees only themselves.
    """
    page = await user_svc.list_all(
        cursor=cursor, limit=limit, invite_state=invite_state, identity=identity
    )
    return UserListResponse(
        data=[_user_response(u) for u in page.data],
        has_more=page.has_more,
        next_cursor=page.next_cursor,
    )


@router.post("/users", status_code=201)
async def create_user(
    body: UserCreateRequest,
    identity: Identity = get_current_identity(required_permissions=["users:write"]),
    user_svc: UserService = Depends(get_user_service),
) -> UserCreatedResponse:
    """Create a new user and issue an invite token."""
    result = await user_svc.create(
        UserCreatePayload(
            email=body.email,
            first_name=body.first_name,
            last_name=body.last_name,
            permissions=body.permissions,
        ),
        identity=identity,
    )
    return UserCreatedResponse(
        user=_user_response(result.user),
        invite_token=result.invite_token,
        invite_expires_at=result.invite_expires_at,
    )


@router.get("/users/{user_id}", responses=with_responses(not_found()))
async def get_user(
    user_id: Annotated[
        str, Path(description="ID of the user.", openapi_examples=ex.example(ex.USER_ID))
    ],
    identity: Identity = get_current_identity(required_permissions=["users:read"]),
    user_svc: UserService = Depends(get_user_service),
) -> UserResponse:
    """Get a user by ID.

    Scoped to the caller: org admins can read any user; anyone else only themselves.
    Any other user answers 404.
    """
    view = await user_svc.get(user_id, identity=identity)
    return _user_response(view)


@router.patch(
    "/users/{user_id}",
    responses=with_responses(
        not_found(), conflict("The email is already in use, or the user has been deleted")
    ),
)
async def update_user(
    user_id: Annotated[
        str, Path(description="ID of the user.", openapi_examples=ex.example(ex.USER_ID))
    ],
    body: UserUpdateRequest,
    identity: Identity = get_current_identity(required_permissions=["users:write"]),
    user_svc: UserService = Depends(get_user_service),
) -> UserResponse:
    """Update a user's profile fields.

    Scoped like `GET /users/{user_id}`: org admins can update any user; anyone
    else only themselves. Any other user answers 404. A deleted user cannot be
    edited (409).
    """
    view = await user_svc.update(
        user_id,
        UserUpdatePayload(
            email=body.email,
            first_name=body.first_name,
            last_name=body.last_name,
        ),
        identity=identity,
    )
    return _user_response(view)


@router.delete(
    "/users/{user_id}",
    status_code=204,
    responses=with_responses(not_found(), conflict(_LAST_ADMIN)),
)
async def delete_user(
    user_id: Annotated[
        str, Path(description="ID of the user.", openapi_examples=ex.example(ex.USER_ID))
    ],
    identity: Identity = get_current_identity(required_permissions=["users:write"]),
    user_svc: UserService = Depends(get_user_service),
) -> Response:
    """Delete a user account (terminal-but-kept).

    The row is retained — the account is anonymized (tombstone email) and
    deactivated so history and audit references stay resolvable — but the
    action is terminal: there is no re-enable arm. For the reversible kill
    switch use ``:disable`` / ``:enable`` instead. Deleting a user also cancels
    their outstanding invites: redeeming one answers 409
    ``invite_already_redeemed``, the same as an invite replaced by a reissue.
    Idempotent: deleting an already-deleted user answers 204 and changes
    nothing.
    """
    await user_svc.delete(user_id, identity=identity)
    return Response(status_code=204)


@router.post(
    "/users/{user_id}:disable",
    status_code=204,
    responses=with_responses(not_found(), conflict(f"{_LAST_ADMIN}, or the user has been deleted")),
)
async def disable_user(
    user_id: Annotated[
        str, Path(description="ID of the user.", openapi_examples=ex.example(ex.USER_ID))
    ],
    identity: Identity = get_current_identity(required_permissions=["users:write"]),
    user_svc: UserService = Depends(get_user_service),
) -> Response:
    """Disable a user account.

    A deleted user is already permanently inactive and cannot be disabled (409).
    """
    await user_svc.disable(user_id, identity=identity)
    return Response(status_code=204)


@router.post(
    "/users/{user_id}:enable",
    status_code=204,
    responses=with_responses(not_found(), conflict("The user has been deleted")),
)
async def enable_user(
    user_id: Annotated[
        str, Path(description="ID of the user.", openapi_examples=ex.example(ex.USER_ID))
    ],
    identity: Identity = get_current_identity(required_permissions=["users:write"]),
    user_svc: UserService = Depends(get_user_service),
) -> Response:
    """Enable a disabled user account.

    A deleted user cannot be re-enabled (409). Login sessions issued before the
    account was disabled stay revoked; the user signs in again.
    """
    await user_svc.enable(user_id, identity=identity)
    return Response(status_code=204)


@router.post(
    "/users/{user_id}:reissue-invite",
    responses=with_responses(
        not_found(), conflict("The user has already redeemed an invite, or has been deleted")
    ),
)
async def reissue_invite(
    user_id: Annotated[
        str, Path(description="ID of the user.", openapi_examples=ex.example(ex.USER_ID))
    ],
    identity: Identity = get_current_identity(required_permissions=["users:write"]),
    user_svc: UserService = Depends(get_user_service),
) -> InviteIssuedResponse:
    """Reissue an invite token for a user."""
    issued = await user_svc.reissue_invite(user_id, identity=identity)
    return InviteIssuedResponse(token=issued.token, expires_at=issued.expires_at)
