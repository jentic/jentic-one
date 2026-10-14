"""User request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from jentic_one.admin.web.schemas.permissions import Permissions
from jentic_one.shared.web.sensitive import SENSITIVE


class UserResponse(BaseModel):
    """User representation in API responses."""

    id: str = Field(description="Unique ID of the user.")
    email: str = Field(description="Email address of the user.")
    first_name: str = Field(description="First name of the user.")
    last_name: str = Field(description="Last name of the user.")
    name: str = Field(description="Full display name: first name and last name.")
    active: bool = Field(description="Whether the user account is enabled.")
    auth_provider: str = Field(
        description="How the user authenticates: `local` (password) or `external`."
    )
    invite_state: str = Field(
        description=(
            "Invitation state: `pending`, `redeemed`, `accepted` or `expired`. `expired` "
            "means a pending invite whose token has lapsed and should be reissued."
        )
    )
    must_change_password: bool = Field(
        description="Whether the user must change their password at next use."
    )
    external_subject_id: str | None = Field(
        default=None,
        description="Subject ID from the external identity provider; null for local users.",
    )
    permissions: Permissions
    created_at: datetime = Field(description="When the user was created (RFC 3339).")
    updated_at: datetime | None = Field(
        default=None,
        description="When the user was last updated (RFC 3339); null if never updated.",
    )


class UserCreateRequest(BaseModel):
    """Request body for creating a new user."""

    email: str = Field(
        min_length=1, max_length=320, description="Email address of the new user; must be unique."
    )
    first_name: str = Field(min_length=1, max_length=255, description="First name of the new user.")
    last_name: str = Field(min_length=1, max_length=255, description="Last name of the new user.")
    permissions: list[str] = Field(
        default=[],
        description=(
            "Permissions to assign to the new user; the caller may only grant permissions "
            "they hold. Defaults to none."
        ),
    )


class UserUpdateRequest(BaseModel):
    """Request body for updating a user."""

    email: str | None = Field(
        default=None,
        min_length=1,
        max_length=320,
        description="New email address; must be unique. Null means no change.",
    )
    first_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="New first name; null means no change.",
    )
    last_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="New last name; null means no change.",
    )


class UserCreatedResponse(BaseModel):
    """Response after user creation including invite token."""

    user: UserResponse
    invite_token: str = Field(
        json_schema_extra=SENSITIVE,
        description="One-time invite token the user redeems to set a password.",
    )
    invite_expires_at: datetime = Field(description="When the invite token expires (RFC 3339).")


class UserListResponse(BaseModel):
    """Paginated list of users."""

    data: list[UserResponse] = Field(description="The page of users.")
    has_more: bool = Field(description="Whether more results exist after this page.")
    next_cursor: str | None = Field(
        default=None, description="Cursor for the next page; null when there are no more results."
    )


class InviteIssuedResponse(BaseModel):
    """Response when an invite token is issued."""

    token: str = Field(
        json_schema_extra=SENSITIVE,
        description="One-time invite token the user redeems to set a password.",
    )
    expires_at: datetime = Field(description="When the invite token expires (RFC 3339).")
