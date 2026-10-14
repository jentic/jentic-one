"""Dynamic Client Registration request/response schemas (RFC 7591 subset)."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from jentic_one.shared.web.sensitive import SENSITIVE

_SCOPE_TOKEN_RE = re.compile(r"^[a-zA-Z0-9_:./-]+$")


class RegisterRequest(BaseModel):
    """POST /register request body."""

    client_name: str = Field(min_length=1, max_length=255, description="Name for the new agent.")
    jwks: dict[str, Any] = Field(
        description="A JSON Web Key Set containing at least one Ed25519 public key"
        " (kty=OKP, crv=Ed25519). RSA and other key types are not accepted.",
        json_schema_extra={
            "example": {
                "keys": [
                    {
                        "kty": "OKP",
                        "crv": "Ed25519",
                        # RFC 8037 appendix A.2 public-key test vector (not a
                        # secret).
                        # pragma: allowlist nextline secret
                        "x": "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo",
                        "kid": "agent-key-1",
                    }
                ]
            }
        },
    )
    grant_types: list[str] | None = Field(
        default=None,
        description=(
            "Accepted for RFC 7591 compatibility and ignored; the agent uses the JWT-bearer grant."
        ),
    )
    token_endpoint_auth_method: str | None = Field(
        default=None,
        description=(
            "Accepted for RFC 7591 compatibility and ignored; the agent uses `private_key_jwt`."
        ),
    )
    scope: str | None = Field(
        default=None,
        max_length=6500,
        description="Space-separated permissions requested for the agent (at most 100 tokens).",
    )

    @field_validator("scope")
    @classmethod
    def validate_scope_tokens(cls, v: str | None) -> str | None:
        if v is None:
            return v
        tokens = v.split()
        if len(tokens) > 100:
            msg = "scope must contain at most 100 tokens"
            raise ValueError(msg)
        for token in tokens:
            if len(token) > 64:
                msg = f"each scope token must be at most 64 characters, got {len(token)}"
                raise ValueError(msg)
            if not _SCOPE_TOKEN_RE.match(token):
                msg = f"scope token '{token}' contains invalid characters"
                raise ValueError(msg)
        return v


class RegisterResponse(BaseModel):
    """POST /register 201 response."""

    client_id: str = Field(description="ID of the new agent, used as its OAuth client_id.")
    registration_access_token: str = Field(
        json_schema_extra=SENSITIVE,
        description=(
            "Bearer token for polling `registration_client_uri`; shown once and expires"
            " after a configured time."
        ),
    )
    registration_client_uri: str = Field(description="URL to poll for the registration status.")
    status: str = Field(
        description="Lifecycle status of the new agent; `pending` until an operator approves it."
    )
    grant_types: list[str] = Field(
        default=["urn:ietf:params:oauth:grant-type:jwt-bearer"],
        description="Grant types the agent may use; always the JWT-bearer grant.",
    )
    token_endpoint_auth_method: str = Field(
        default="private_key_jwt",
        description="How the agent authenticates at the token endpoint; always `private_key_jwt`.",
    )
    # Opaque, single-use ownership-claim token. Present only when the deployment
    # installs a claim-token minter (multi-user deployments); the registering
    # human presents it to POST /agents/{id}:claim to take ownership. Omitted on
    # the OSS single-user default (no minter → no claim flow). Marked sensitive so
    # the CLI's Layer-1 redactor masks it in output — it is a bearer capability.
    claim_token: str | None = Field(
        default=None,
        json_schema_extra=SENSITIVE,
        description=(
            "Single-use token a human presents to `POST /agents/{agent_id}:claim` to take"
            " ownership; omitted when the deployment does not issue claim tokens."
        ),
    )


class RegistrationStatusResponse(BaseModel):
    """GET /register/{agent_id} response."""

    client_id: str = Field(description="ID of the agent, used as its OAuth client_id.")
    status: str = Field(
        description=(
            "Lifecycle status of the agent: `pending`, `active`, `rejected`, `disabled`"
            " or `archived`."
        )
    )
    grant_types: list[str] = Field(
        default=["urn:ietf:params:oauth:grant-type:jwt-bearer"],
        description="Grant types the agent may use; always the JWT-bearer grant.",
    )
    token_endpoint_auth_method: str = Field(
        default="private_key_jwt",
        description="How the agent authenticates at the token endpoint; always `private_key_jwt`.",
    )
