"""Auth request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from jentic_one.admin.services._support.passwords import MIN_PASSWORD_LENGTH
from jentic_one.shared.web.sensitive import SENSITIVE


class LoginRequest(BaseModel):
    """Credentials for login."""

    email: str = Field(min_length=1, max_length=320, description="Email address of the user.")
    password: str = Field(
        min_length=1,
        max_length=128,
        json_schema_extra=SENSITIVE,
        description="The user's password.",
    )


class LoginResponse(BaseModel):
    """JWT token response after successful authentication."""

    access_token: str = Field(
        json_schema_extra=SENSITIVE,
        description="Signed JWT to send as a bearer token on subsequent requests.",
    )
    token_type: str = Field(description="Token type; always `bearer`.")
    expires_in: int = Field(description="Lifetime of the access token in seconds.")
    must_change_password: bool = Field(
        description="Whether the user must change their password before using other endpoints."
    )


class ChangePasswordRequest(BaseModel):
    """Payload for changing own password."""

    current_password: str = Field(
        min_length=1,
        max_length=128,
        json_schema_extra=SENSITIVE,
        description="The caller's current password.",
    )
    new_password: str = Field(
        min_length=MIN_PASSWORD_LENGTH,
        max_length=128,
        json_schema_extra=SENSITIVE,
        description="The new password to set.",
    )


class RedeemInviteRequest(BaseModel):
    """Payload for redeeming an invite token."""

    invite_token: str = Field(
        min_length=1,
        max_length=512,
        json_schema_extra=SENSITIVE,
        description="The invite token issued when the user was created or re-invited.",
    )
    password: str = Field(
        min_length=MIN_PASSWORD_LENGTH,
        max_length=128,
        json_schema_extra=SENSITIVE,
        description="Password to set for the invited user.",
    )


class CreateAdminRequest(BaseModel):
    """Payload for first-run admin creation (one-time setup)."""

    email: str = Field(
        min_length=1, max_length=320, description="Email address of the first admin."
    )
    password: str = Field(
        min_length=MIN_PASSWORD_LENGTH,
        max_length=128,
        json_schema_extra=SENSITIVE,
        description="Password for the first admin.",
    )
    first_name: str = Field(
        default="Admin",
        min_length=1,
        max_length=100,
        description="First name of the first admin; defaults to `Admin`.",
    )
    last_name: str = Field(
        default="User",
        min_length=1,
        max_length=100,
        description="Last name of the first admin; defaults to `User`.",
    )


class CurrentUserResponse(BaseModel):
    """Current user profile."""

    id: str = Field(description="Unique ID of the user.")
    email: str = Field(description="Email address of the user.")
    first_name: str = Field(description="First name of the user.")
    last_name: str = Field(description="Last name of the user.")
    active: bool = Field(description="Whether the user account is enabled.")
    permissions: list[str] = Field(
        description="The user's effective permissions, including those implied by assigned ones."
    )
    must_change_password: bool = Field(
        description="Whether the user must change their password before using other endpoints."
    )
    created_at: datetime = Field(description="When the user was created (RFC 3339).")
    updated_at: datetime | None = Field(
        default=None,
        description="When the user was last updated (RFC 3339); null if never updated.",
    )
