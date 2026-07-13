"""OAuth token endpoint request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TokenRequest(BaseModel):
    """Token endpoint request (JSON body).

    Note: this endpoint accepts application/json, not the
    application/x-www-form-urlencoded encoding that RFC 6749 specifies.
    """

    grant_type: str
    refresh_token: str | None = None
    assertion: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    code: str | None = None
    code_verifier: str | None = None
    redirect_uri: str | None = None


class TokenResponse(BaseModel):
    """Token endpoint success response."""

    access_token: str
    refresh_token: str | None = None
    id_token: str | None = None
    token_type: str = "bearer"
    expires_in: int


class MintRequest(BaseModel):
    """Ephemeral token minting request."""

    scope: str
    target_agent_id: str
    ttl_seconds: int | None = Field(default=None, ge=1, le=3600)


class MintResponse(BaseModel):
    """Ephemeral token minting response."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int


class RevokeRequest(BaseModel):
    """Revocation endpoint request (JSON body)."""

    token: str
    token_type_hint: str | None = None


class IntrospectRequest(BaseModel):
    """Introspection endpoint request (JSON body)."""

    token: str
    token_type_hint: str | None = None


class IntrospectResponse(BaseModel):
    """RFC 7662 introspection response."""

    active: bool
    sub: str | None = None
    scope: str | None = None
    exp: int | None = None
    token_type: str | None = None
