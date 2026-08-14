"""Dynamic Client Registration request/response schemas (RFC 7591 subset)."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

_SCOPE_TOKEN_RE = re.compile(r"^[a-zA-Z0-9_:./-]+$")


class RegisterRequest(BaseModel):
    """POST /register request body."""

    # Bounded to the agents.name column (String(255)): /register is
    # unauthenticated per RFC 7591, and the name flows verbatim into event
    # summaries surfaced on operator attention surfaces.
    client_name: str = Field(min_length=1, max_length=255)
    jwks: dict[str, Any]
    grant_types: list[str] | None = None
    token_endpoint_auth_method: str | None = None
    scope: str | None = Field(default=None, max_length=6500)

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

    client_id: str
    registration_access_token: str
    registration_client_uri: str
    status: str
    grant_types: list[str] = ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
    token_endpoint_auth_method: str = "private_key_jwt"
    # Opaque, single-use ownership-claim token. Present only when the deployment
    # installs a claim-token minter (multi-user deployments); the registering
    # human presents it to POST /agents/{id}:claim to take ownership. Omitted on
    # the OSS single-user default (no minter → no claim flow).
    claim_token: str | None = None


class RegistrationStatusResponse(BaseModel):
    """GET /register/{agent_id} response."""

    client_id: str
    status: str
    grant_types: list[str] = ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
    token_endpoint_auth_method: str = "private_key_jwt"
