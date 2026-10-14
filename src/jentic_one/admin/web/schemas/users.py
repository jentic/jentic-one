"""User request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from jentic_one.admin.web.schemas.permissions import Permissions
from jentic_one.shared.web import openapi_examples as ex
from jentic_one.shared.web.sensitive import SENSITIVE


class UserResponse(BaseModel):
    """User representation in API responses."""

    id: str = Field(description="Unique ID of the user.", examples=[ex.USER_ID])
    email: str = Field(description="Email address of the user.", examples=["ada@example.com"])
    first_name: str = Field(description="First name of the user.", examples=["Ada"])
    last_name: str = Field(description="Last name of the user.", examples=["Lovelace"])
    name: str = Field(
        description="Full display name: first name and last name.", examples=["Ada Lovelace"]
    )
    active: bool = Field(description="Whether the user account is enabled.", examples=[True])
    auth_provider: str = Field(
        description="How the user authenticates: `local` (password) or `external`.",
        examples=["external"],
    )
    invite_state: str = Field(
        description=(
            "Invitation state: `pending`, `redeemed`, `accepted` or `expired`. `expired` "
            "means a pending invite whose token has lapsed and should be reissued."
        ),
        examples=["accepted"],
    )
    must_change_password: bool = Field(
        description="Whether the user must change their password at next use.", examples=[False]
    )
    external_subject_id: str | None = Field(
        default=None,
        description="Subject ID from the external identity provider; null for local users.",
        examples=["auth0|6568b3984d1c238dbe7e2e29"],
    )
    permissions: Permissions
    created_at: datetime = Field(
        description="When the user was created (RFC 3339).", examples=["2026-01-15T09:30:00Z"]
    )
    updated_at: datetime | None = Field(
        default=None,
        description="When the user was last updated (RFC 3339); null if never updated.",
        examples=["2026-01-20T14:05:00Z"],
    )


class UserCreateRequest(BaseModel):
    """Request body for creating a new user."""

    email: str = Field(
        min_length=1,
        max_length=320,
        description="Email address of the new user; must be unique.",
        examples=["ada@example.com"],
    )
    first_name: str = Field(
        min_length=1, max_length=255, description="First name of the new user.", examples=["Ada"]
    )
    last_name: str = Field(
        min_length=1,
        max_length=255,
        description="Last name of the new user.",
        examples=["Lovelace"],
    )
    permissions: list[str] = Field(
        default=[],
        description=(
            "Permissions to assign to the new user; the caller may only grant permissions "
            "they hold. Defaults to none."
        ),
        examples=[["users:read", "agents:read"]],
    )


class UserUpdateRequest(BaseModel):
    """Request body for updating a user."""

    email: str | None = Field(
        default=None,
        min_length=1,
        max_length=320,
        description="New email address; must be unique. Null means no change.",
        examples=["ada.lovelace@example.com"],
    )
    first_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="New first name; null means no change.",
        examples=["Ada"],
    )
    last_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="New last name; null means no change.",
        examples=["Lovelace"],
    )


class UserCreatedResponse(BaseModel):
    """Response after user creation including invite token."""

    user: UserResponse
    invite_token: str = Field(
        json_schema_extra=SENSITIVE,
        description="One-time invite token the user redeems to set a password.",
        examples=[ex.OPAQUE_TOKEN],
    )
    invite_expires_at: datetime = Field(
        description="When the invite token expires (RFC 3339).", examples=["2026-01-22T09:30:00Z"]
    )


class UserListResponse(BaseModel):
    """Paginated list of users."""

    data: list[UserResponse] = Field(description="The page of users.")
    has_more: bool = Field(
        description="Whether more results exist after this page.", examples=[True]
    )
    next_cursor: str | None = Field(
        default=None,
        description="Cursor for the next page; null when there are no more results.",
        examples=[ex.CURSOR],
    )


class InviteIssuedResponse(BaseModel):
    """Response when an invite token is issued."""

    token: str = Field(
        json_schema_extra=SENSITIVE,
        description="One-time invite token the user redeems to set a password.",
        examples=[ex.OPAQUE_TOKEN],
    )
    expires_at: datetime = Field(
        description="When the invite token expires (RFC 3339).", examples=["2026-01-22T09:30:00Z"]
    )
