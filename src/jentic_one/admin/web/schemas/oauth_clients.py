"""OAuth client request/response schemas for the admin web layer."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from jentic_one.shared.web.sensitive import SENSITIVE


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
        "An empty list denies all non-OIDC scopes. Null means unrestricted "
        "(all scopes permitted).",
    )
    token_endpoint_auth_method: Literal["client_secret_basic", "none"] = Field(
        default="client_secret_basic",
        description="Client authentication method at the token endpoint. "
        "``client_secret_basic`` creates a confidential client with a generated "
        "secret; ``none`` creates a public (secret-less) client that relies on "
        "PKCE alone — no secret is generated or returned.",
    )
    consent_model: Literal["user", "agent"] = Field(
        default="user",
        description="What a user's consent grants for this client. ``user`` "
        "keeps today's act-as-user semantics; ``agent`` marks the client for "
        "agent-bound consent (the MCP path).",
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
        "an empty list denies all non-OIDC scopes; "
        'the wildcard ``["*"]`` resets to unrestricted.',
    )


_OAUTH_CLIENT_EXAMPLE: dict[str, JsonValue] = {
    "id": "oac_2NxYz...",
    "client_id": "oc_abc123...",
    "name": "my-app-production",
    "description": "My application production deployment",
    "redirect_uris": ["https://app.example.com/auth/callback"],
    "allowed_scopes": ["agents:read", "credentials:read"],
    "active": True,
    "require_consent": True,
    "token_endpoint_auth_method": "client_secret_basic",
    "consent_model": "user",
    "registration_source": "admin",
    # Non-null example values throughout: FastAPI's OpenAPI encoder strips
    # ``None`` from examples, which would drop these required-but-nullable
    # fields and make the example fail its own schema.
    "software_id": "com.example.my-app",
    "approval_status": "approved",
    "active_grant_count": 0,
    "created_at": "2026-08-18T12:00:00Z",
    "updated_at": "2026-08-18T12:00:00Z",
    "created_by": "usr_abc123",
}


class OAuthClientResponse(BaseModel):
    """An OAuth client in API responses."""

    model_config = ConfigDict(json_schema_extra={"examples": [_OAUTH_CLIENT_EXAMPLE]})

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
    token_endpoint_auth_method: str = Field(
        description="Client authentication method at the token endpoint: "
        "``client_secret_basic`` (confidential) or ``none`` (public, PKCE-only)."
    )
    consent_model: str = Field(
        description="What a user's consent grants for this client: ``user`` or ``agent``."
    )
    registration_source: str = Field(
        description="How the client entered the registry: ``admin`` or ``dcr``."
    )
    software_id: str | None = Field(
        description="RFC 7591 software identifier claimed at registration, if any."
    )
    approval_status: str = Field(
        description="Admin approval lifecycle: ``pending``, ``approved``, or ``denied``. "
        "Only approved clients may enter OAuth flows; ``active`` remains the "
        "independent kill switch."
    )
    active_grant_count: int = Field(
        default=0,
        description="Number of active consent→agent grants for this client. "
        "Computed on the read endpoints (list/get); "
        "write-path responses report 0.",
    )
    created_at: datetime
    updated_at: datetime | None
    created_by: str | None


class OAuthClientCreateResponse(OAuthClientResponse):
    """Returned on creation — includes the one-time plaintext client secret."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    **_OAUTH_CLIENT_EXAMPLE,
                    "client_secret": "<redacted>",  # pragma: allowlist secret
                }
            ]
        }
    )

    client_secret: str | None = Field(
        description="The client secret. Shown only once at creation — store it "
        "securely. Null for public (secret-less) clients.",
        json_schema_extra=SENSITIVE,
    )


class OAuthClientDenyRequest(BaseModel):
    """Request body for denying an OAuth client."""

    reason: str | None = Field(
        default=None,
        max_length=1024,
        description="Optional reason recorded in the audit trail.",
    )


class OAuthClientRotateSecretResponse(BaseModel):
    """Returned on secret rotation — the new one-time plaintext secret."""

    client_secret: str = Field(
        description="The new client secret. Store it securely; the previous secret is now invalid.",
        json_schema_extra=SENSITIVE,
    )


class OAuthClientListResponse(BaseModel):
    """A list of OAuth clients."""

    data: list[OAuthClientResponse]
