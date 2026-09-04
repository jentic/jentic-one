"""OAuth token endpoint request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field

from jentic_one.shared.web.sensitive import SENSITIVE


class TokenRequest(BaseModel):
    """Token endpoint request — accepts both JSON and form-encoded (RFC 6749)."""

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
    scope: str = Field(
        description="Space-delimited effective scopes of the minted access token "
        "(RFC 6749 §3.3). Always present (RFC 6749 §5.1): the platform downscopes "
        "(client ceiling / consent-grant intersection), so the granted set may be "
        "narrower than requested and clients must not assume they got what they "
        "asked for."
    )


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
    """Revocation endpoint request (RFC 7009) — JSON or form-encoded.

    ``token`` is required either way. ``token_type_hint``
    (``access_token``/``refresh_token``) is a lookup-order optimization only —
    the server falls through both types regardless (RFC 7009 §2.1).
    ``client_id`` belongs to the form-encoded public-client arm (G11): the
    secret-less client's lineage binding; ignored on the bearer-authenticated
    JSON arm, where the platform identity scopes the revocation instead.
    """

    token: str = Field(json_schema_extra=SENSITIVE)
    token_type_hint: str | None = None
    client_id: str | None = None


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
