"""Auth request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from jentic_one.admin.services._support.passwords import MIN_PASSWORD_LENGTH
from jentic_one.shared.web.sensitive import SENSITIVE


class LoginRequest(BaseModel):
    """Credentials for login."""

    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=128, json_schema_extra=SENSITIVE)


class LoginResponse(BaseModel):
    """JWT token response after successful authentication."""

    access_token: str = Field(json_schema_extra=SENSITIVE)
    token_type: str
    expires_in: int
    must_change_password: bool


class ChangePasswordRequest(BaseModel):
    """Payload for changing own password."""

    current_password: str = Field(min_length=1, max_length=128, json_schema_extra=SENSITIVE)
    new_password: str = Field(
        min_length=MIN_PASSWORD_LENGTH, max_length=128, json_schema_extra=SENSITIVE
    )


class RedeemInviteRequest(BaseModel):
    """Payload for redeeming an invite token."""

    invite_token: str = Field(min_length=1, max_length=512, json_schema_extra=SENSITIVE)
    password: str = Field(
        min_length=MIN_PASSWORD_LENGTH, max_length=128, json_schema_extra=SENSITIVE
    )


class CreateAdminRequest(BaseModel):
    """Payload for first-run admin creation (one-time setup)."""

    email: str = Field(min_length=1, max_length=320)
    password: str = Field(
        min_length=MIN_PASSWORD_LENGTH, max_length=128, json_schema_extra=SENSITIVE
    )
    first_name: str = Field(default="Admin", min_length=1, max_length=100)
    last_name: str = Field(default="User", min_length=1, max_length=100)


class CurrentUserResponse(BaseModel):
    """Current user profile."""

    id: str
    email: str
    first_name: str
    last_name: str
    active: bool
    permissions: list[str]
    must_change_password: bool
    created_at: datetime
    updated_at: datetime | None = None
