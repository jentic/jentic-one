"""Service-layer Pydantic models for credential operations."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from jentic_one.control.services.credentials.schemas.provision import APIReference
from jentic_one.shared.models.credentials import CredentialType


class ProviderDiscoveryEntry(BaseModel):
    """Metadata about a single configured credential provider."""

    id: str
    label: str
    managed: bool
    types: list[CredentialType]
    configured: bool
    callback_url: str | None = None


class CredentialCreate(BaseModel):
    """Payload for creating a credential."""

    type: CredentialType
    name: str
    api: APIReference
    provider: str = "static"
    server_variables: dict[str, str] | None = None

    # bearer_token fields
    token: str | None = None

    # api_key fields
    key: str | None = None
    location: str | None = None
    field_name: str | None = None

    # basic fields
    username: str | None = None
    password: str | None = None

    # oauth2 fields
    grant_type: str | None = None
    token_url: str | None = None
    authorize_url: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    scopes: list[str] | None = None


class CredentialUpdate(BaseModel):
    """Payload for updating/rotating a credential."""

    type: CredentialType
    name: str | None = None
    active: bool | None = None
    server_variables: dict[str, str] | None = None

    # bearer_token rotation
    token: str | None = None

    # api_key rotation/update
    key: str | None = None
    location: str | None = None
    field_name: str | None = None

    # basic rotation/update
    username: str | None = None
    password: str | None = None

    # oauth2 rotation/update
    client_secret: str | None = None
    token_url: str | None = None
    scopes: list[str] | None = None


class BearerTokenFull(BaseModel):
    """Full bearer token details (shown once on create)."""

    token: str


class ApiKeyFull(BaseModel):
    """Full API key details (shown once on create)."""

    key: str
    location: str
    field_name: str


class BasicAuthFull(BaseModel):
    """Full basic auth details (shown once on create)."""

    username: str
    password: str


class OAuth2Full(BaseModel):
    """Full OAuth2 details (shown once on create)."""

    client_id: str
    client_secret: str
    token_url: str
    grant_type: str
    scopes: list[str] | None = None


class NoAuthFull(BaseModel):
    """Placeholder detail block for a no-auth credential (no secret)."""


class BearerTokenRedacted(BaseModel):
    """Redacted bearer token details."""

    token_preview: str | None = None


class ApiKeyRedacted(BaseModel):
    """Redacted API key details."""

    key_preview: str | None = None
    location: str | None = None
    field_name: str | None = None


class BasicAuthRedacted(BaseModel):
    """Redacted basic auth details."""

    username: str


class OAuth2Redacted(BaseModel):
    """Redacted OAuth2 details."""

    client_id: str
    token_url: str
    grant_type: str
    scopes: list[str] | None = None


class NoAuthRedacted(BaseModel):
    """Redacted detail block for a no-auth credential (no secret)."""


class CredentialFullView(BaseModel):
    """Create response — echoes the secret block once."""

    credential_id: str
    type: CredentialType
    name: str
    api: APIReference
    provider: str
    active: bool
    created_at: datetime
    server_variables: dict[str, str] | None = None
    secret: BearerTokenFull | ApiKeyFull | BasicAuthFull | OAuth2Full | NoAuthFull


class CredentialRedactedView(BaseModel):
    """Read/list/patch response — previews only, no cleartext."""

    credential_id: str
    type: CredentialType
    name: str
    api: APIReference
    provider: str
    provider_account_ref: str | None = None
    active: bool
    created_at: datetime
    updated_at: datetime | None = None
    details: (
        BearerTokenRedacted
        | ApiKeyRedacted
        | BasicAuthRedacted
        | OAuth2Redacted
        | NoAuthRedacted
    )
    server_variables: dict[str, str] | None = None

    model_config = {"from_attributes": True}


class CredentialPage(BaseModel):
    """Paginated list of redacted credentials."""

    data: list[CredentialRedactedView]
    has_more: bool
    next_cursor: str | None = None
