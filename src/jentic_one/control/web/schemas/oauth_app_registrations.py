"""Web request/response models for the OAuth app registration admin API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from jentic_one.control.services.oauth_app_registrations.schemas import (
    OAuthAppRegistrationFlowKind,
)
from jentic_one.shared.web.sensitive import SENSITIVE


class OAuthAppRegistrationResponse(BaseModel):
    """Registration as returned to admins.

    Never includes the client secret — only ``has_client_secret`` +
    ``secret_last_rotated_at`` so an admin can see rotation history without
    being able to read the material.
    """

    id: str
    name: str
    api_vendor: str
    flow_kind: OAuthAppRegistrationFlowKind
    client_id: str
    is_active: bool
    has_client_secret: bool
    secret_last_rotated_at: datetime | None
    authorize_url: str | None = None
    token_url: str | None = None
    authorization_endpoint: str | None = None
    token_endpoint: str | None = None
    default_scopes: list[str] | None = None
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    dependent_credential_count: int


class OAuthAppRegistrationListResponse(BaseModel):
    """List envelope for OAuth app registrations."""

    data: list[OAuthAppRegistrationResponse]


class AuthorizationCodeRegistrationCreateRequest(BaseModel):
    """Create request for an authorization-code registration."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "name": "MyOrg GitHub",
                    "api_vendor": "github",
                    "flow_kind": "authorization_code",
                    "client_id": "Iv1.a1b2c3d4",
                    "client_secret": "<paste-client-secret-from-vendor-console>",
                    "authorize_url": "https://github.com/login/oauth/authorize",
                    "token_url": "https://github.com/login/oauth/access_token",
                    "default_scopes": ["repo", "read:user"],
                }
            ]
        }
    )

    name: Annotated[str, Field(min_length=1, max_length=255)]
    api_vendor: Annotated[str, Field(min_length=1, max_length=100)]
    flow_kind: Literal[OAuthAppRegistrationFlowKind.AUTHORIZATION_CODE]
    client_id: Annotated[str, Field(min_length=1, max_length=255)]
    client_secret: Annotated[str, Field(min_length=1, json_schema_extra=SENSITIVE)]
    authorize_url: Annotated[str, Field(min_length=1, max_length=2048)]
    token_url: Annotated[str, Field(min_length=1, max_length=2048)]
    default_scopes: list[str] | None = None


class DeviceAuthorizationRegistrationCreateRequest(BaseModel):
    """Create request for a device-authorization registration."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "name": "MyOrg GitHub (device flow)",
                    "api_vendor": "github",
                    "flow_kind": "device_authorization",
                    "client_id": "Iv1.a1b2c3d4",
                    "authorization_endpoint": "https://github.com/login/device/code",
                    "token_endpoint": "https://github.com/login/oauth/access_token",
                    "default_scopes": ["repo"],
                }
            ]
        }
    )

    name: Annotated[str, Field(min_length=1, max_length=255)]
    api_vendor: Annotated[str, Field(min_length=1, max_length=100)]
    flow_kind: Literal[OAuthAppRegistrationFlowKind.DEVICE_AUTHORIZATION]
    client_id: Annotated[str, Field(min_length=1, max_length=255)]
    authorization_endpoint: Annotated[str, Field(min_length=1, max_length=2048)]
    token_endpoint: Annotated[str, Field(min_length=1, max_length=2048)]
    default_scopes: list[str] | None = None


OAuthAppRegistrationCreateRequest = (
    AuthorizationCodeRegistrationCreateRequest | DeviceAuthorizationRegistrationCreateRequest
)


class OAuthAppRegistrationUpdateRequest(BaseModel):
    """Partial update — fields not present are left untouched."""

    name: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    is_active: bool | None = None
    default_scopes: list[str] | None = None
    authorize_url: Annotated[str, Field(min_length=1, max_length=2048)] | None = None
    token_url: Annotated[str, Field(min_length=1, max_length=2048)] | None = None
    authorization_endpoint: Annotated[str, Field(min_length=1, max_length=2048)] | None = None
    token_endpoint: Annotated[str, Field(min_length=1, max_length=2048)] | None = None


class OAuthAppRegistrationRotateSecretRequest(BaseModel):
    """Rotate the client secret on an auth-code registration."""

    client_secret: Annotated[str, Field(min_length=1, json_schema_extra=SENSITIVE)]
