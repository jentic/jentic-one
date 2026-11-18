"""Service-layer schemas for OAuth app registrations.

Kept distinct from the web-layer request/response models: these are the domain
inputs and views at the service boundary, so no HTTP-only concern (aliases,
examples) lives here.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, SecretStr


class OAuthAppRegistrationFlowKind(StrEnum):
    """Which OAuth flow a registration supports."""

    AUTHORIZATION_CODE = "authorization_code"
    DEVICE_AUTHORIZATION = "device_authorization"


class OAuthAppRegistrationCreate(BaseModel):
    """Shared input fields for registering an OAuth application."""

    name: str
    api_vendor: str
    catalog_api_id: str
    display_name: str
    client_id: str
    default_scopes: list[str] | None


class AuthorizationCodeRegistrationCreate(OAuthAppRegistrationCreate):
    """Service input for registering an authorization-code OAuth application."""

    client_secret: SecretStr
    authorize_url: str
    token_url: str


class DeviceAuthorizationRegistrationCreate(OAuthAppRegistrationCreate):
    """Service input for registering a device-authorization OAuth application."""

    authorization_endpoint: str
    token_endpoint: str


class OAuthAppRegistrationUpdate(BaseModel):
    """Partial update input for an OAuth application registration."""

    name: str | None = None
    display_name: str | None = None
    default_scopes: list[str] | None = None
    authorize_url: str | None = None
    token_url: str | None = None
    authorization_endpoint: str | None = None
    token_endpoint: str | None = None
    is_active: bool | None = None


class OAuthAppRegistrationQuery(BaseModel):
    """Filter and pagination parameters for listing OAuth app registrations."""

    api_vendor: str | None = None
    flow_kind: str | None = None
    include_inactive: bool = False
    cursor: str | None = None
    limit: int = 50


class OAuthAppRegistrationView(BaseModel):
    """Registration view exposed to admins.

    Never carries the client secret — only a ``has_client_secret`` flag and
    the last-rotated timestamp. The write API keeps the secret write-only.
    """

    id: str
    name: str
    api_vendor: str
    # Catalog API slug the OAuth app targets.
    catalog_api_id: str
    # Vendor family label ("Gmail").
    display_name: str
    flow_kind: OAuthAppRegistrationFlowKind
    client_id: str
    is_active: bool
    has_client_secret: bool
    secret_last_rotated_at: dt.datetime | None
    authorize_url: str | None
    token_url: str | None
    authorization_endpoint: str | None
    token_endpoint: str | None
    default_scopes: list[str] | None
    created_at: dt.datetime
    updated_at: dt.datetime
    created_by: str | None
    dependent_credential_count: int
