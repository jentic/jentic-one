"""Dynamic Client Registration request/response schemas (RFC 7591 subset)."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

_SCOPE_TOKEN_RE = re.compile(r"^[a-zA-Z0-9_:./-]+$")


class RegisterRequest(BaseModel):
    """POST /register request body."""

    client_name: str
    jwks: dict[str, Any] = Field(
        description="A JSON Web Key Set containing at least one Ed25519 public key"
        " (kty=OKP, crv=Ed25519). RSA and other key types are not accepted."
    )
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


class RegistrationStatusResponse(BaseModel):
    """GET /register/{agent_id} response."""

    client_id: str
    status: str
    grant_types: list[str] = ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
    token_endpoint_auth_method: str = "private_key_jwt"
