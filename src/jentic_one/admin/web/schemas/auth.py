"""Auth request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from jentic_one.admin.services._support.passwords import MIN_PASSWORD_LENGTH
from jentic_one.shared.web import openapi_examples as ex
from jentic_one.shared.web.sensitive import SENSITIVE


class LoginRequest(BaseModel):
    """Credentials for login."""

    email: str = Field(
        min_length=1,
        max_length=320,
        description="Email address of the user.",
        examples=["ada@example.com"],
    )
    password: str = Field(
        min_length=1,
        max_length=128,
        json_schema_extra=SENSITIVE,
        description="The user's password.",
        examples=["example-password"],  # pragma: allowlist secret
    )


class LoginResponse(BaseModel):
    """JWT token response after successful authentication."""

    access_token: str = Field(
        json_schema_extra=SENSITIVE,
        description="Signed JWT to send as a bearer token on subsequent requests.",
        examples=["example-access-token"],  # pragma: allowlist secret
    )
    token_type: str = Field(description="Token type; always `bearer`.", examples=["bearer"])
    expires_in: int = Field(description="Lifetime of the access token in seconds.", examples=[3600])
    must_change_password: bool = Field(
        description="Whether the user must change their password before using other endpoints.",
        examples=[False],
    )


class ChangePasswordRequest(BaseModel):
    """Payload for changing own password."""

    current_password: str = Field(
        min_length=1,
        max_length=128,
        json_schema_extra=SENSITIVE,
        description="The caller's current password.",
        examples=["example-password"],  # pragma: allowlist secret
    )
    new_password: str = Field(
        min_length=MIN_PASSWORD_LENGTH,
        max_length=128,
        json_schema_extra=SENSITIVE,
        description="The new password to set.",
        examples=["example-new-password"],  # pragma: allowlist secret
    )


class RedeemInviteRequest(BaseModel):
    """Payload for redeeming an invite token."""

    invite_token: str = Field(
        min_length=1,
        max_length=512,
        json_schema_extra=SENSITIVE,
        description="The invite token issued when the user was created or re-invited.",
        examples=[ex.OPAQUE_TOKEN],
    )
    password: str = Field(
        min_length=MIN_PASSWORD_LENGTH,
        max_length=128,
        json_schema_extra=SENSITIVE,
        description="Password to set for the invited user.",
        examples=["example-password"],  # pragma: allowlist secret
    )


class CreateAdminRequest(BaseModel):
    """Payload for first-run admin creation (one-time setup)."""

    email: str = Field(
        min_length=1,
        max_length=320,
        description="Email address of the first admin.",
        examples=["admin@example.com"],
    )
    password: str = Field(
        min_length=MIN_PASSWORD_LENGTH,
        max_length=128,
        json_schema_extra=SENSITIVE,
        description="Password for the first admin.",
        examples=["example-password"],  # pragma: allowlist secret
    )
    first_name: str = Field(
        default="Admin",
        min_length=1,
        max_length=100,
        description="First name of the first admin; defaults to `Admin`.",
        examples=["Ada"],
    )
    last_name: str = Field(
        default="User",
        min_length=1,
        max_length=100,
        description="Last name of the first admin; defaults to `User`.",
        examples=["Lovelace"],
    )


class CurrentUserResponse(BaseModel):
    """Current user profile."""

    id: str = Field(description="Unique ID of the user.", examples=[ex.USER_ID])
    email: str = Field(description="Email address of the user.", examples=["ada@example.com"])
    first_name: str = Field(description="First name of the user.", examples=["Ada"])
    last_name: str = Field(description="Last name of the user.", examples=["Lovelace"])
    active: bool = Field(description="Whether the user account is enabled.", examples=[True])
    permissions: list[str] = Field(
        description="The user's effective permissions, including those implied by assigned ones.",
        examples=[["users:write", "users:read", "agents:read"]],
    )
    must_change_password: bool = Field(
        description="Whether the user must change their password before using other endpoints.",
        examples=[False],
    )
    created_at: datetime = Field(
        description="When the user was created (RFC 3339).", examples=["2026-01-15T09:30:00Z"]
    )
    updated_at: datetime | None = Field(
        default=None,
        description="When the user was last updated (RFC 3339); null if never updated.",
        examples=["2026-01-20T14:05:00Z"],
    )
