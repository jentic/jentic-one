"""User request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from jentic_one.admin.web.schemas.permissions import Permissions
from jentic_one.shared.web.sensitive import SENSITIVE


class UserResponse(BaseModel):
    """User representation in API responses."""

    id: str
    email: str
    first_name: str
    last_name: str
    name: str
    active: bool
    auth_provider: str
    invite_state: str
    must_change_password: bool
    external_subject_id: str | None = None
    permissions: Permissions
    created_at: datetime
    updated_at: datetime | None = None


class UserCreateRequest(BaseModel):
    """Request body for creating a new user."""

    email: str = Field(min_length=1, max_length=320)
    first_name: str = Field(min_length=1, max_length=255)
    last_name: str = Field(min_length=1, max_length=255)
    permissions: list[str] = []


class UserUpdateRequest(BaseModel):
    """Request body for updating a user."""

    email: str | None = Field(default=None, min_length=1, max_length=320)
    first_name: str | None = Field(default=None, min_length=1, max_length=255)
    last_name: str | None = Field(default=None, min_length=1, max_length=255)


class UserCreatedResponse(BaseModel):
    """Response after user creation including invite token."""

    user: UserResponse
    invite_token: str = Field(json_schema_extra=SENSITIVE)
    invite_expires_at: datetime


class UserListResponse(BaseModel):
    """Paginated list of users."""

    data: list[UserResponse]
    has_more: bool
    next_cursor: str | None = None


class InviteIssuedResponse(BaseModel):
    """Response when an invite token is issued."""

    token: str = Field(json_schema_extra=SENSITIVE)
    expires_at: datetime
