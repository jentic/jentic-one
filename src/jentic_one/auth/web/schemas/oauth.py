"""OAuth token endpoint request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field

from jentic_one.shared.web.sensitive import SENSITIVE


class TokenRequest(BaseModel):
    """Token endpoint request (JSON body — not RFC 6749 form-encoded)."""

    grant_type: str
    refresh_token: str | None = Field(default=None, json_schema_extra=SENSITIVE)
    assertion: str | None = Field(default=None, json_schema_extra=SENSITIVE)
    client_id: str | None = None
    client_secret: str | None = Field(default=None, json_schema_extra=SENSITIVE)
    code: str | None = None
    code_verifier: str | None = None
    redirect_uri: str | None = None


class TokenResponse(BaseModel):
    """Token endpoint success response."""

    access_token: str = Field(json_schema_extra=SENSITIVE)
    refresh_token: str | None = Field(default=None, json_schema_extra=SENSITIVE)
    id_token: str | None = Field(default=None, json_schema_extra=SENSITIVE)
    token_type: str = "bearer"
    expires_in: int


class MintRequest(BaseModel):
    """Ephemeral token minting request."""

    scope: str
    target_agent_id: str
    ttl_seconds: int | None = Field(default=None, ge=1, le=3600)


class MintResponse(BaseModel):
    """Ephemeral token minting response."""

    access_token: str = Field(json_schema_extra=SENSITIVE)
    token_type: str = "bearer"
    expires_in: int


class RevokeRequest(BaseModel):
    """Revocation endpoint request (form body)."""

    token: str = Field(json_schema_extra=SENSITIVE)
    token_type_hint: str | None = None


class IntrospectRequest(BaseModel):
    """Introspection endpoint request (form body)."""

    token: str = Field(json_schema_extra=SENSITIVE)
    token_type_hint: str | None = None


class IntrospectResponse(BaseModel):
    """RFC 7662 introspection response."""

    active: bool
    sub: str | None = None
    scope: str | None = None
    exp: int | None = None
    token_type: str | None = None
