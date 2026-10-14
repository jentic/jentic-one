"""Web request/response models for the credentials API."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from jentic_one.control.web.schemas.permission_rules import (
    PermissionRuleReadSchema,
    PermissionRuleSchema,
)
from jentic_one.shared.models.credentials import CredentialLocation, CredentialType
from jentic_one.shared.schemas import APIReference as APIReferenceResponse
from jentic_one.shared.schemas import APIReferenceRequest
from jentic_one.shared.web.sensitive import SENSITIVE

__all__ = ["APIReferenceRequest", "APIReferenceResponse"]

_MAX_SERVER_VARS = 20
_MAX_SERVER_VAR_KEY_LEN = 128
_MAX_SERVER_VAR_VALUE_LEN = 512


class RuntimeConfig(BaseModel):
    """Optional per-upstream-call overrides."""

    headers: dict[str, str] | None = Field(
        default=None, description="Header overrides for upstream calls."
    )
    query_params: dict[str, str] | None = Field(
        default=None, description="Query-parameter overrides for upstream calls."
    )


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


# C0 controls (incl. CR/LF/TAB/NUL) and DEL. These values are injected verbatim
# into upstream request headers / query parameters, where a control character is
# either illegal (the HTTP client rejects the request) or a header-splitting
# vector, so reject them at the edge.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _reject_control_chars(v: str | None, info: ValidationInfo) -> str | None:
    """Reject control characters in a value injected into an upstream request.

    The message names the field only — never the value, which may be a secret.
    """
    if v is not None and _CONTROL_CHARS.search(v):
        raise ValueError(f"{info.field_name} must not contain control characters")
    return v


# --- Create request models (per type) ---


class BearerTokenCreateRequest(BaseModel):
    """Create request for bearer_token credentials."""

    type: Literal["bearer_token"] = Field(description="Credential type; always `bearer_token`.")
    name: str = Field(description="Human-readable label for the credential.")
    api: APIReferenceRequest
    provider: str = Field(
        default="static", description="Credential provider; 'static' for stored secrets."
    )
    runtime_config: RuntimeConfig | None = Field(
        default=None, description="Per-upstream-call overrides; reserved and not currently applied."
    )
    server_variables: dict[str, str] | None = Field(
        default=None,
        description="OpenAPI server-variable values for URL template substitution (at most 20).",
    )
    token: str = Field(
        description="The bearer token secret. Stored encrypted; never returned after create.",
        json_schema_extra=SENSITIVE,
    )

    _check_server_variables = field_validator("server_variables")(_validate_server_variables)
    _check_control_chars = field_validator("token")(_reject_control_chars)


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

    type: Literal["api_key"] = Field(description="Credential type; always `api_key`.")
    name: str = Field(description="Human-readable label for the credential.")
    api: APIReferenceRequest = Field(
        description="Loose (vendor, name, version) API identity tuple."
    )
    provider: str = Field(
        default="static", description="Credential provider; 'static' for stored secrets."
    )
    runtime_config: RuntimeConfig | None = Field(
        default=None, description="Per-upstream-call overrides; reserved and not currently applied."
    )
    server_variables: dict[str, str] | None = Field(
        default=None,
        description="OpenAPI server-variable values for URL template substitution (at most 20).",
    )
    key: str = Field(
        description="The API key secret. Stored encrypted; never returned after create.",
        json_schema_extra=SENSITIVE,
    )
    location: CredentialLocation = Field(description="Where to inject the key on upstream calls.")
    field_name: str = Field(description="Header or query-parameter name carrying the key.")

    _check_server_variables = field_validator("server_variables")(_validate_server_variables)
    _check_control_chars = field_validator("key", "field_name")(_reject_control_chars)


class BasicAuthCreateRequest(BaseModel):
    """Create request for basic credentials."""

    type: Literal["basic"] = Field(description="Credential type; always `basic`.")
    name: str = Field(description="Human-readable label for the credential.")
    api: APIReferenceRequest
    provider: str = Field(
        default="static", description="Credential provider; 'static' for stored secrets."
    )
    runtime_config: RuntimeConfig | None = Field(
        default=None, description="Per-upstream-call overrides; reserved and not currently applied."
    )
    server_variables: dict[str, str] | None = Field(
        default=None,
        description="OpenAPI server-variable values for URL template substitution (at most 20).",
    )
    username: str = Field(description="Username sent with HTTP Basic auth.")
    password: str = Field(
        description="Password for HTTP Basic auth. Stored encrypted; never returned after create.",
        json_schema_extra=SENSITIVE,
    )

    _check_server_variables = field_validator("server_variables")(_validate_server_variables)


class OAuth2CreateRequest(BaseModel):
    """Create request for oauth2 credentials.

    For managed providers (e.g. pipedream, direct_oauth2), token_url/client_id/client_secret
    are optional — the connect flow handles authentication without caller-supplied client details.
    """

    type: Literal["oauth2"] = Field(description="Credential type; always `oauth2`.")
    name: str = Field(description="Human-readable label for the credential.")
    api: APIReferenceRequest
    provider: str = Field(
        default="static", description="Credential provider; 'static' for stored secrets."
    )
    runtime_config: RuntimeConfig | None = Field(
        default=None, description="Per-upstream-call overrides; reserved and not currently applied."
    )
    server_variables: dict[str, str] | None = Field(
        default=None,
        description="OpenAPI server-variable values for URL template substitution (at most 20).",
    )
    grant_type: str = Field(
        default="client_credentials",
        description="OAuth2 grant type (e.g. client_credentials, authorization_code, device_code).",
    )
    token_url: str | None = Field(
        default=None,
        description="Token endpoint URL; required for non-managed providers.",
    )
    authorize_url: str | None = Field(
        default=None,
        description=(
            "Authorization endpoint URL; for the device_code grant, the device "
            "authorization endpoint."
        ),
    )
    client_id: str | None = Field(
        default=None, description="OAuth client ID; required for non-managed providers."
    )
    client_secret: str | None = Field(
        default=None,
        description=(
            "OAuth client secret; required for non-managed providers except the device_code "
            "grant. Stored encrypted; never returned after create."
        ),
        json_schema_extra=SENSITIVE,
    )
    scopes: list[str] | None = Field(default=None, description="OAuth scopes to request.")

    _check_server_variables = field_validator("server_variables")(_validate_server_variables)


class NoAuthCreateRequest(BaseModel):
    """Create request for no_auth credentials.

    A no-auth credential carries no secret — it represents "this API is called
    without authentication". It still exists as a credential row so an agent
    binding (and its permission rules) can hang off it, and the broker resolves
    it as a no-op auth (see broker credential resolver / injection).
    """

    type: Literal["no_auth"] = Field(description="Credential type; always `no_auth`.")
    name: str = Field(description="Human-readable label for the credential.")
    api: APIReferenceRequest
    provider: str = Field(
        default="static", description="Credential provider; 'static' for stored secrets."
    )
    runtime_config: RuntimeConfig | None = Field(
        default=None, description="Per-upstream-call overrides; reserved and not currently applied."
    )
    server_variables: dict[str, str] | None = Field(
        default=None,
        description="OpenAPI server-variable values for URL template substitution (at most 20).",
    )

    _check_server_variables = field_validator("server_variables")(_validate_server_variables)


class Sigv4CreateRequest(BaseModel):
    """Create request for sigv4 (AWS Signature V4) credentials."""

    type: Literal["sigv4"] = Field(description="Credential type; always `sigv4`.")
    name: str = Field(description="Human-readable label for the credential.")
    api: APIReferenceRequest = Field(
        description="Loose (vendor, name, version) API identity tuple."
    )
    provider: str = Field(
        default="static", description="Credential provider; 'static' for stored secrets."
    )
    runtime_config: RuntimeConfig | None = Field(
        default=None, description="Per-upstream-call overrides; reserved and not currently applied."
    )
    server_variables: dict[str, str] | None = Field(
        default=None,
        description="OpenAPI server-variable values for URL template substitution (at most 20).",
    )
    access_key_id: str = Field(description="AWS access key id (public identifier).")
    secret_access_key: str = Field(
        description="AWS secret access key. Stored encrypted; never returned after create.",
        json_schema_extra=SENSITIVE,
    )
    session_token: str | None = Field(
        default=None,
        description=(
            "Optional temporary-credential session token (STS). Expires; re-save when it does."
        ),
        json_schema_extra=SENSITIVE,
    )
    aws_region: str = Field(description="Signing region, e.g. 'us-east-1'.")
    aws_service: str = Field(description="Signing service, e.g. 'aoss', 'execute-api', 's3'.")

    _check_server_variables = field_validator("server_variables")(_validate_server_variables)
    # Region/service ride the ``Authorization`` credential scope, so they are
    # header material too.
    _check_control_chars = field_validator(
        "access_key_id", "session_token", "aws_region", "aws_service"
    )(_reject_control_chars)


CredentialCreateRequest = Annotated[
    BearerTokenCreateRequest
    | ApiKeyCreateRequest
    | BasicAuthCreateRequest
    | OAuth2CreateRequest
    | NoAuthCreateRequest
    | Sigv4CreateRequest,
    Field(discriminator="type"),
]


# --- Update request models ---


class BearerTokenUpdateRequest(BaseModel):
    """Update request for bearer_token credentials."""

    type: Literal["bearer_token"] = Field(description="Credential type; always `bearer_token`.")
    name: str | None = Field(
        default=None, description="New label for the credential; omit to leave unchanged."
    )
    active: bool | None = Field(
        default=None,
        description="Enable or disable the credential for injection; omit to leave unchanged.",
    )
    runtime_config: RuntimeConfig | None = Field(
        default=None, description="Per-upstream-call overrides; reserved and not currently applied."
    )
    server_variables: dict[str, str] | None = Field(
        default=None,
        description="Replacement OpenAPI server-variable values; omit to leave unchanged.",
    )
    token: str | None = Field(
        default=None,
        description="Replacement bearer token; omit to leave unchanged.",
        json_schema_extra=SENSITIVE,
    )

    _check_server_variables = field_validator("server_variables")(_validate_server_variables)
    _check_control_chars = field_validator("token")(_reject_control_chars)


class ApiKeyUpdateRequest(BaseModel):
    """Update request for api_key credentials."""

    type: Literal["api_key"] = Field(description="Credential type; always `api_key`.")
    name: str | None = Field(
        default=None, description="New label for the credential; omit to leave unchanged."
    )
    active: bool | None = Field(
        default=None,
        description="Enable or disable the credential for injection; omit to leave unchanged.",
    )
    runtime_config: RuntimeConfig | None = Field(
        default=None, description="Per-upstream-call overrides; reserved and not currently applied."
    )
    server_variables: dict[str, str] | None = Field(
        default=None,
        description="Replacement OpenAPI server-variable values; omit to leave unchanged.",
    )
    key: str | None = Field(
        default=None,
        description="Replacement API key; omit to leave unchanged.",
        json_schema_extra=SENSITIVE,
    )
    location: CredentialLocation | None = Field(
        default=None,
        description=(
            "Immutable after create. Accepted for backward compatibility; if "
            "provided it must equal the stored value, otherwise the request is "
            "rejected. Recreate the credential to change the injection binding."
        ),
    )
    field_name: str | None = Field(
        default=None,
        description=(
            "Immutable after create. Accepted for backward compatibility; if "
            "provided it must equal the stored value, otherwise the request is "
            "rejected. Recreate the credential to change the parameter name."
        ),
    )

    _check_server_variables = field_validator("server_variables")(_validate_server_variables)
    _check_control_chars = field_validator("key", "field_name")(_reject_control_chars)


class BasicAuthUpdateRequest(BaseModel):
    """Update request for basic credentials."""

    type: Literal["basic"] = Field(description="Credential type; always `basic`.")
    name: str | None = Field(
        default=None, description="New label for the credential; omit to leave unchanged."
    )
    active: bool | None = Field(
        default=None,
        description="Enable or disable the credential for injection; omit to leave unchanged.",
    )
    runtime_config: RuntimeConfig | None = Field(
        default=None, description="Per-upstream-call overrides; reserved and not currently applied."
    )
    server_variables: dict[str, str] | None = Field(
        default=None,
        description="Replacement OpenAPI server-variable values; omit to leave unchanged.",
    )
    username: str | None = Field(
        default=None, description="Replacement username; omit to leave unchanged."
    )
    password: str | None = Field(
        default=None,
        description="Replacement password; omit to leave unchanged.",
        json_schema_extra=SENSITIVE,
    )

    _check_server_variables = field_validator("server_variables")(_validate_server_variables)


class OAuth2UpdateRequest(BaseModel):
    """Update request for oauth2 credentials."""

    type: Literal["oauth2"] = Field(description="Credential type; always `oauth2`.")
    name: str | None = Field(
        default=None, description="New label for the credential; omit to leave unchanged."
    )
    active: bool | None = Field(
        default=None,
        description="Enable or disable the credential for injection; omit to leave unchanged.",
    )
    runtime_config: RuntimeConfig | None = Field(
        default=None, description="Per-upstream-call overrides; reserved and not currently applied."
    )
    server_variables: dict[str, str] | None = Field(
        default=None,
        description="Replacement OpenAPI server-variable values; omit to leave unchanged.",
    )
    client_secret: str | None = Field(
        default=None,
        description=(
            "Replacement client secret; omit to leave unchanged. token_url and scopes "
            "are applied only together with it."
        ),
        json_schema_extra=SENSITIVE,
    )
    token_url: str | None = Field(
        default=None,
        description="Replacement token endpoint URL; applied only when client_secret is sent.",
    )
    scopes: list[str] | None = Field(
        default=None,
        description="Replacement OAuth scopes; applied only when client_secret is sent.",
    )

    _check_server_variables = field_validator("server_variables")(_validate_server_variables)


class Sigv4UpdateRequest(BaseModel):
    """Update request for sigv4 credentials (key rotation / scope edit)."""

    type: Literal["sigv4"] = Field(description="Credential type; always `sigv4`.")
    name: str | None = Field(
        default=None, description="New label for the credential; omit to leave unchanged."
    )
    active: bool | None = Field(
        default=None,
        description="Enable or disable the credential for injection; omit to leave unchanged.",
    )
    runtime_config: RuntimeConfig | None = Field(
        default=None, description="Per-upstream-call overrides; reserved and not currently applied."
    )
    server_variables: dict[str, str] | None = Field(
        default=None,
        description="Replacement OpenAPI server-variable values; omit to leave unchanged.",
    )
    access_key_id: str | None = Field(
        default=None,
        description="Replacement AWS access key ID; must be sent with secret_access_key.",
    )
    secret_access_key: str | None = Field(
        default=None,
        description="Replacement AWS secret access key; must be sent with access_key_id.",
        json_schema_extra=SENSITIVE,
    )
    session_token: str | None = Field(
        default=None,
        description="Replacement STS session token; omit to leave unchanged.",
        json_schema_extra=SENSITIVE,
    )
    clear_session_token: bool = Field(
        default=False,
        description="Remove the stored session token; takes precedence over session_token.",
    )
    aws_region: str | None = Field(
        default=None, description="Replacement signing region; omit to leave unchanged."
    )
    aws_service: str | None = Field(
        default=None, description="Replacement signing service; omit to leave unchanged."
    )

    _check_server_variables = field_validator("server_variables")(_validate_server_variables)
    # Region/service ride the ``Authorization`` credential scope, so they are
    # header material too.
    _check_control_chars = field_validator(
        "access_key_id", "session_token", "aws_region", "aws_service"
    )(_reject_control_chars)


CredentialUpdateRequest = Annotated[
    BearerTokenUpdateRequest
    | ApiKeyUpdateRequest
    | BasicAuthUpdateRequest
    | OAuth2UpdateRequest
    | Sigv4UpdateRequest,
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
    catalog_api_id: str | None = Field(
        default=None,
        description=(
            "Catalog identity slug of the target API (`domain[/sub-api]`), when "
            "recorded at create time. Display-only; null for older credentials."
        ),
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
        description=(
            "Redacted, type-specific projection (hints/last-N chars; never the "
            "secret). For oauth2: client_id, token_url, grant_type "
            "(authorization_code | client_credentials), scopes, and — for "
            "authorization_code only — `connected`, whether the interactive "
            "sign-in completed and is still usable (null for other grants)."
        ),
    )
    server_variables: dict[str, str] | None = Field(
        default=None,
        description="OpenAPI server-variable values for URL template substitution.",
    )
    oauth_app_registration_id: str | None = Field(
        default=None,
        description=(
            "Id of the shared ``oauth_app_registrations`` row this credential "
            "was minted through, if any. Null for legacy embedded OAuth "
            "credentials and every non-OAuth type."
        ),
    )
    oauth_app_registration_name: str | None = Field(
        default=None,
        description=(
            "Admin-facing name of the shared OAuth app registration this "
            "credential was minted through. Null when the credential is not "
            "backed by a shared registration."
        ),
    )


class CredentialCreateResponse(BaseModel):
    """Create response: redacted + secret shown once."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "credential": {
                        "credential_id": "cred_01HZX9...",
                        "type": "api_key",
                        "name": "Stripe live key",
                        "api": {"vendor": "stripe-com", "name": "stripe", "version": "2024-04-10"},
                        "provider": "static",
                        "active": True,
                        "created_at": "2026-01-15T09:30:00Z",
                    },
                    "secret": {"key": "sk_live_…"},
                    "warnings": None,
                },
                {
                    "credential": {
                        "credential_id": "cred_01HZXA...",
                        "type": "bearer_token",
                        "name": "PostHog personal key",
                        "api": {"vendor": "posthog-com", "name": "posthog-api", "version": "1.0"},
                        "provider": "static",
                        "active": True,
                        "created_at": "2026-01-15T09:31:00Z",
                    },
                    "secret": {"token": "phx_…"},
                    "warnings": [
                        "API scope 'posthog-com/posthog-api/1.0' matches no imported API — "
                        "executions using this credential will fail. Import a matching API, "
                        "or delete this credential and re-create it scoped to an imported "
                        "identity; imported APIs for this vendor: "
                        "posthog-com/posthog-com-posthog-api (1.0)"
                    ],
                },
            ]
        }
    )

    credential: CredentialRedactedResponse
    secret: dict[str, Any] = Field(
        description="Type-specific secret material, returned only in this create response.",
        json_schema_extra=SENSITIVE,
    )
    warnings: list[str] | None = Field(
        default=None,
        description=(
            "Advisory warnings from create — e.g. the credential's API scope "
            "matches no imported API identity, so execution through it would "
            "fail until a matching API is imported. The credential is created "
            "regardless; null when there is nothing to flag."
        ),
    )


class CredentialListResponse(BaseModel):
    """Paginated list of credentials."""

    data: list[CredentialRedactedResponse] = Field(description="The page of credentials.")
    has_more: bool = Field(description="Whether more results exist after this page.")
    next_cursor: str | None = Field(
        default=None, description="Cursor for the next page; null when there are no more results."
    )


# --- Connect flow models ---


class ConnectRequestBody(BaseModel):
    """Request body for initiating a credential connect flow."""

    scopes: list[str] = Field(default_factory=list, description="OAuth scopes to request.")
    extra: dict[str, str] = Field(
        default_factory=dict, description="Additional provider-specific connect parameters."
    )


class AuthCodeConnectChallengeResponse(BaseModel):
    """Authorization-code redirect challenge.

    Client opens ``authorize_url`` in a popup; completion lands
    server-side via ``/credentials/oauth/callback``.
    """

    kind: Literal["authorization_code"] = "authorization_code"
    authorize_url: str
    state: str


class DeviceAuthorizationConnectChallengeResponse(BaseModel):
    """RFC 8628 device-code challenge.

    Client shows ``user_code`` at ``verification_uri`` and polls
    ``GET /credentials/{id}`` until state moves off ``pending``
    (scanner-driven server-side).
    """

    kind: Literal["device_authorization"] = "device_authorization"
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None = None
    poll_interval_seconds: int | None = None


ConnectChallengeResponse = Annotated[
    AuthCodeConnectChallengeResponse | DeviceAuthorizationConnectChallengeResponse,
    Field(discriminator="kind"),
]


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
        default=None,
        description=(
            "OAuth2 redirect URI for providers that require it. When no explicit "
            "redirect_uri is configured, this is derived from the deployment's "
            "public origin (server.public_base_url or the request origin), so it "
            "reflects the exact callback the connect flow will register with the "
            "IdP. Add this URL to your OAuth app's allowed redirect URIs."
        ),
    )


class ProviderDiscoveryResponse(BaseModel):
    """Discovery response listing all available credential providers."""

    providers: list[ProviderDiscoveryEntryResponse] = Field(
        description="The available credential providers."
    )


class CredentialAgentResponse(BaseModel):
    """Agent directly bound to a credential (theme 5 phase 1)."""

    agent_id: str = Field(description="ID of the bound agent.")
    agent_name: str = Field(description="Name of the bound agent.")
    status: str = Field(description="Status of the bound agent.")
    bound_at: datetime = Field(description="When the agent was bound to the credential (RFC 3339).")
    suspended: bool = Field(
        description=(
            "True when the binding is soft-suspended (reversible cut-off): the "
            "binding and its rules survive, but the agent cannot execute "
            "through this credential until resumed."
        )
    )
    rule_set_id: str | None = Field(
        default=None,
        description=(
            "Shared permission rule set this binding points at, if any. While "
            "attached, the set's ordered list is the binding's effective "
            "policy; null means the binding's inline rules apply."
        ),
    )


class CredentialAgentListResponse(BaseModel):
    """Paginated list of agents directly bound to a credential."""

    data: list[CredentialAgentResponse] = Field(description="The page of bound agents.")
    has_more: bool = Field(description="Whether more results exist after this page.")
    next_cursor: str | None = Field(
        default=None, description="Cursor for the next page; null when there are no more results."
    )


class RuleSetAttachRequest(BaseModel):
    """Point a direct agent↔credential binding at a shared rule set."""

    rule_set_id: str = Field(description="Id (`prs_…`) of the rule set to attach.")


class RuleSetCreateRequest(BaseModel):
    """Create a shared permission rule set (theme 5 phase 1, Q-04)."""

    name: str = Field(max_length=255, description="Unique human-readable name.")
    description: str | None = Field(
        default=None, max_length=1000, description="Optional free-text description of the set."
    )
    rules: Annotated[list[PermissionRuleSchema], Field(max_length=100)] = Field(
        default_factory=list,
        description="Initial ordered rule list (first-match-wins, default-deny).",
    )


class RuleSetUpdateRequest(BaseModel):
    """Rename or re-describe a rule set."""

    name: str | None = Field(
        default=None, max_length=255, description="New unique name; omit to leave unchanged."
    )
    description: str | None = Field(
        default=None, max_length=1000, description="New description; omit to leave unchanged."
    )


class RuleSetSummaryResponse(BaseModel):
    """Rule set list entry."""

    rule_set_id: str = Field(description="Unique ID of the rule set (`prs_…`).")
    name: str = Field(description="Unique human-readable name of the rule set.")
    description: str | None = Field(
        default=None, description="Free-text description of the rule set; null if unset."
    )
    rule_count: int = Field(description="Number of rules in the set.")
    curated: bool = Field(
        description=(
            "True when an org admin created the set. A curated set can be attached by any "
            "caller allowed to write a binding's rules and edited only by an org admin; "
            "any other set is attachable and editable by its creator or an org admin."
        )
    )
    created_by: str | None = Field(
        default=None, description="ID of the actor that created the rule set."
    )
    created_at: datetime = Field(description="When the rule set was created (RFC 3339).")


class RuleSetResponse(BaseModel):
    """Rule set detail — the ordered rules plus its referencing-binding count."""

    rule_set_id: str = Field(description="Unique ID of the rule set (`prs_…`).")
    name: str = Field(description="Unique human-readable name of the rule set.")
    description: str | None = Field(
        default=None, description="Free-text description of the rule set; null if unset."
    )
    rules: list[PermissionRuleReadSchema] = Field(
        description="Ordered rule list (first-match-wins, default-deny)."
    )
    binding_count: int = Field(
        description="How many agent-credential bindings currently point at this set."
    )
    curated: bool = Field(
        description=(
            "True when an org admin created the set. A curated set can be attached by any "
            "caller allowed to write a binding's rules and edited only by an org admin; "
            "any other set is attachable and editable by its creator or an org admin."
        )
    )
    created_by: str | None = Field(
        default=None, description="ID of the actor that created the rule set."
    )
    created_at: datetime = Field(description="When the rule set was created (RFC 3339).")


class RuleSetListResponse(BaseModel):
    """Paginated list of rule sets."""

    data: list[RuleSetSummaryResponse] = Field(description="The page of rule sets.")
    has_more: bool = Field(description="Whether more results exist after this page.")
    next_cursor: str | None = Field(
        default=None, description="Cursor for the next page; null when there are no more results."
    )
