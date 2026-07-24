"""Web request/response models for the credentials API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from jentic_one.shared.models.credentials import CredentialLocation, CredentialType
from jentic_one.shared.schemas import APIReference as APIReferenceResponse
from jentic_one.shared.schemas import APIReferenceRequest

__all__ = ["APIReferenceRequest", "APIReferenceResponse"]

_MAX_SERVER_VARS = 20
_MAX_SERVER_VAR_KEY_LEN = 128
_MAX_SERVER_VAR_VALUE_LEN = 512


class RuntimeConfig(BaseModel):
    """Optional per-upstream-call overrides."""

    headers: dict[str, str] | None = None
    query_params: dict[str, str] | None = None


def _validate_server_variables(v: dict[str, str] | None) -> dict[str, str] | None:
    if v is None:
        return None
    if len(v) > _MAX_SERVER_VARS:
        raise ValueError(f"server_variables may contain at most {_MAX_SERVER_VARS} entries")
    for key, value in v.items():
        if len(key) > _MAX_SERVER_VAR_KEY_LEN:
            raise ValueError(f"server_variables key exceeds {_MAX_SERVER_VAR_KEY_LEN} characters")
        if len(value) > _MAX_SERVER_VAR_VALUE_LEN:
            raise ValueError(
                f"server_variables value exceeds {_MAX_SERVER_VAR_VALUE_LEN} characters"
            )
    return v


# --- Create request models (per type) ---


class BearerTokenCreateRequest(BaseModel):
    """Create request for bearer_token credentials."""

    type: Literal["bearer_token"]
    name: str
    api: APIReferenceRequest
    provider: str = "static"
    runtime_config: RuntimeConfig | None = None
    server_variables: dict[str, str] | None = None
    token: str

    _check_server_variables = field_validator("server_variables")(_validate_server_variables)


class ApiKeyCreateRequest(BaseModel):
    """Create request for api_key credentials."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "type": "api_key",
                    "name": "Stripe live key",
                    "api": {"vendor": "stripe.com", "name": "stripe", "version": "2024-04-10"},
                    "provider": "static",
                    "key": "sk_live_…",
                    "location": "header",
                    "field_name": "Authorization",
                }
            ]
        }
    )

    type: Literal["api_key"]
    name: str = Field(description="Human-readable label for the credential.")
    api: APIReferenceRequest = Field(
        description="Loose (vendor, name, version) API identity tuple."
    )
    provider: str = Field(
        default="static", description="Credential provider; 'static' for stored secrets."
    )
    runtime_config: RuntimeConfig | None = None
    server_variables: dict[str, str] | None = None
    key: str = Field(
        description="The API key secret. Stored encrypted; never returned after create."
    )
    location: CredentialLocation = Field(description="Where to inject the key on upstream calls.")
    field_name: str = Field(description="Header or query-parameter name carrying the key.")

    _check_server_variables = field_validator("server_variables")(_validate_server_variables)


class BasicAuthCreateRequest(BaseModel):
    """Create request for basic credentials."""

    type: Literal["basic"]
    name: str
    api: APIReferenceRequest
    provider: str = "static"
    runtime_config: RuntimeConfig | None = None
    server_variables: dict[str, str] | None = None
    username: str
    password: str

    _check_server_variables = field_validator("server_variables")(_validate_server_variables)


class OAuth2CreateRequest(BaseModel):
    """Create request for oauth2 credentials.

    For managed providers (e.g. pipedream, direct_oauth2), token_url/client_id/client_secret
    are optional — the connect flow handles authentication without caller-supplied client details.
    """

    type: Literal["oauth2"]
    name: str
    api: APIReferenceRequest
    provider: str = "static"
    runtime_config: RuntimeConfig | None = None
    server_variables: dict[str, str] | None = None
    grant_type: str = "client_credentials"
    token_url: str | None = None
    authorize_url: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    scopes: list[str] | None = None

    _check_server_variables = field_validator("server_variables")(_validate_server_variables)


class NoAuthCreateRequest(BaseModel):
    """Create request for no_auth credentials.

    A no-auth credential carries no secret — it represents "this API is called
    without authentication". It still exists as a credential row so a toolkit
    binding (and its permission rules) can hang off it, and the broker resolves
    it as a no-op auth (see broker credential resolver / injection).
    """

    type: Literal["no_auth"]
    name: str
    api: APIReferenceRequest
    provider: str = "static"
    runtime_config: RuntimeConfig | None = None
    server_variables: dict[str, str] | None = None

    _check_server_variables = field_validator("server_variables")(_validate_server_variables)


CredentialCreateRequest = Annotated[
    BearerTokenCreateRequest
    | ApiKeyCreateRequest
    | BasicAuthCreateRequest
    | OAuth2CreateRequest
    | NoAuthCreateRequest,
    Field(discriminator="type"),
]


# --- Update request models ---


class BearerTokenUpdateRequest(BaseModel):
    """Update request for bearer_token credentials."""

    type: Literal["bearer_token"]
    name: str | None = None
    active: bool | None = None
    runtime_config: RuntimeConfig | None = None
    server_variables: dict[str, str] | None = None
    token: str | None = None

    _check_server_variables = field_validator("server_variables")(_validate_server_variables)


class ApiKeyUpdateRequest(BaseModel):
    """Update request for api_key credentials."""

    type: Literal["api_key"]
    name: str | None = None
    active: bool | None = None
    runtime_config: RuntimeConfig | None = None
    server_variables: dict[str, str] | None = None
    key: str | None = None
    location: CredentialLocation | None = None
    field_name: str | None = None

    _check_server_variables = field_validator("server_variables")(_validate_server_variables)


class BasicAuthUpdateRequest(BaseModel):
    """Update request for basic credentials."""

    type: Literal["basic"]
    name: str | None = None
    active: bool | None = None
    runtime_config: RuntimeConfig | None = None
    server_variables: dict[str, str] | None = None
    username: str | None = None
    password: str | None = None

    _check_server_variables = field_validator("server_variables")(_validate_server_variables)


class OAuth2UpdateRequest(BaseModel):
    """Update request for oauth2 credentials."""

    type: Literal["oauth2"]
    name: str | None = None
    active: bool | None = None
    runtime_config: RuntimeConfig | None = None
    server_variables: dict[str, str] | None = None
    client_secret: str | None = None
    token_url: str | None = None
    scopes: list[str] | None = None

    _check_server_variables = field_validator("server_variables")(_validate_server_variables)


CredentialUpdateRequest = Annotated[
    BearerTokenUpdateRequest | ApiKeyUpdateRequest | BasicAuthUpdateRequest | OAuth2UpdateRequest,
    Field(discriminator="type"),
]


# --- Response models ---


class BearerTokenSecretResponse(BaseModel):
    """Full bearer token secret (shown once on create)."""

    token: str


class ApiKeySecretResponse(BaseModel):
    """Full API key secret (shown once on create)."""

    key: str
    location: str
    field_name: str


class BasicAuthSecretResponse(BaseModel):
    """Full basic auth secret (shown once on create)."""

    username: str
    password: str


class OAuth2SecretResponse(BaseModel):
    """Full OAuth2 secret (shown once on create)."""

    client_id: str
    client_secret: str
    token_url: str
    grant_type: str
    scopes: list[str] | None = None


class CredentialRedactedResponse(BaseModel):
    """Redacted credential response (for read/list/patch)."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "credential_id": "cred_01HZX9...",
                    "type": "api_key",
                    "name": "Stripe live key",
                    "api": {"vendor": "stripe.com", "name": "stripe", "version": "2024-04-10"},
                    "provider": "static",
                    "active": True,
                    "created_at": "2026-01-15T09:30:00Z",
                    "details": {
                        "location": "header",
                        "field_name": "Authorization",
                        "hint": "…live_abcd",
                    },
                }
            ]
        }
    )

    credential_id: str = Field(description="Stable credential identifier, prefixed `cred_`.")
    type: CredentialType = Field(
        description="Credential auth type (api_key, bearer_token, basic, oauth2)."
    )
    name: str = Field(description="Human-readable label.")
    api: APIReferenceResponse = Field(
        description="The (vendor, name, version) API this credential targets."
    )
    provider: str = Field(description="Credential provider; 'static' for stored secrets.")
    provider_account_ref: str | None = Field(
        default=None, description="Opaque reference to the provider account, when applicable."
    )
    active: bool = Field(description="Whether the credential is enabled for injection.")
    created_by: str | None = Field(
        default=None, description="Identity that created the credential (its owner)."
    )
    created_at: datetime = Field(description="Creation timestamp (UTC).")
    updated_at: datetime | None = Field(default=None, description="Last update timestamp (UTC).")
    details: dict[str, Any] | None = Field(
        default=None,
        description="Redacted, type-specific projection (hints/last-N chars; never the secret).",
    )
    server_variables: dict[str, str] | None = Field(
        default=None,
        description="OpenAPI server-variable values for URL template substitution.",
    )


class CredentialCreateResponse(BaseModel):
    """Create response: redacted + secret shown once."""

    credential: CredentialRedactedResponse
    secret: dict[str, Any]


class CredentialListResponse(BaseModel):
    """Paginated list of credentials."""

    data: list[CredentialRedactedResponse]
    has_more: bool
    next_cursor: str | None = None


# --- Connect flow models ---


class ConnectRequestBody(BaseModel):
    """Request body for initiating a credential connect flow."""

    scopes: list[str] = Field(default_factory=list)
    extra: dict[str, str] = Field(default_factory=dict)


class ConnectChallengeResponse(BaseModel):
    """Response from a connect initiation."""

    authorize_url: str
    state: str


class ProviderDiscoveryEntryResponse(BaseModel):
    """Discovery metadata for a single credential provider."""

    id: str = Field(description="Provider identifier (registry key).")
    label: str = Field(description="Human-readable provider name.")
    managed: bool = Field(
        description="Whether the provider handles vendor sign-in on behalf of the user."
    )
    types: list[CredentialType] = Field(
        description="Wire-level credential types this provider supports."
    )
    configured: bool = Field(
        description="Whether the provider is fully configured and operational."
    )
    callback_url: str | None = Field(
        default=None, description="OAuth2 redirect URI for providers that require it."
    )


class ProviderDiscoveryResponse(BaseModel):
    """Discovery response listing all available credential providers."""

    providers: list[ProviderDiscoveryEntryResponse]
