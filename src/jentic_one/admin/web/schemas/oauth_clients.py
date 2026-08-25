"""OAuth client request/response schemas for the admin web layer."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

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
    description: str | None = Field(default=None, description="Optional description of the client.")
    redirect_uris: list[Annotated[str, Field(max_length=2048)]] = Field(
        description="Allowed OAuth callback URLs. At least one required.",
        min_length=1,
        max_length=20,
    )
    require_consent: bool = Field(
        default=True,
        description="Whether to show a consent screen during authorization. "
        "Set to false for trusted first-party integrations.",
    )
    allowed_scopes: list[str] | None = Field(
        default=None,
        description="If set, restricts which scopes this client may request. "
        "Null means all scopes are permitted.",
    )


class OAuthClientUpdateRequest(BaseModel):
    """Request body for updating an OAuth client."""

    name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    redirect_uris: list[Annotated[str, Field(max_length=2048)]] | None = Field(
        default=None, min_length=1, max_length=20
    )
    active: bool | None = None
    require_consent: bool | None = None
    allowed_scopes: list[str] | None = Field(
        default=None,
        description="Scope restriction list. Null means no change; "
        "an empty list clears the restriction (all scopes permitted).",
    )


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
    allowed_scopes: list[str] | None = Field(
        description="Scopes this client may request. Null means unrestricted."
    )
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
