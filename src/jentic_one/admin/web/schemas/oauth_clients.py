"""OAuth client request/response schemas for the admin web layer."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OAuthClientCreateRequest(BaseModel):
    """Request body for creating an OAuth client."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "name": "my-app-production",
                    "description": "My application production deployment",
                    "redirect_uris": ["https://app.example.com/auth/callback"],
                    "require_consent": True,
                }
            ]
        }
    )

    name: str = Field(description="Human-readable name for the client.", max_length=255)
    description: str | None = Field(
        default=None, description="Optional description of the client."
    )
    redirect_uris: list[str] = Field(
        description="Allowed OAuth callback URLs. At least one required.",
        min_length=1,
    )
    require_consent: bool = Field(
        default=True,
        description="Whether to show a consent screen during authorization. "
        "Set to false for trusted first-party integrations.",
    )


class OAuthClientUpdateRequest(BaseModel):
    """Request body for updating an OAuth client."""

    name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    redirect_uris: list[str] | None = Field(default=None, min_length=1)
    active: bool | None = None
    require_consent: bool | None = None


class OAuthClientResponse(BaseModel):
    """An OAuth client in API responses."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "id": "oac_2NxYz...",
                    "client_id": "oc_abc123...",
                    "name": "my-app-production",
                    "description": "My application production deployment",
                    "redirect_uris": ["https://app.example.com/auth/callback"],
                    "active": True,
                    "require_consent": True,
                    "created_at": "2026-08-18T12:00:00Z",
                    "updated_at": None,
                    "created_by": "usr_abc123",
                }
            ]
        }
    )

    id: str = Field(description="Internal ID (ksuid).")
    client_id: str = Field(description="Public client identifier used in OAuth flows.")
    name: str
    description: str | None
    redirect_uris: list[str]
    active: bool
    require_consent: bool = Field(
        description="Whether a consent screen is shown during authorization."
    )
    created_at: datetime
    updated_at: datetime | None
    created_by: str | None


class OAuthClientListResponse(BaseModel):
    """A list of OAuth clients."""

    data: list[OAuthClientResponse]
