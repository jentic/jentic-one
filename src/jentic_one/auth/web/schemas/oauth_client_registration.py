"""Anonymous OAuth-client DCR request/response schemas (RFC 7591 subset).

Schemas for ``POST /oauth-clients`` — the anonymous-DCR front door. Unknown
metadata fields are ignored (RFC 7591 servers SHOULD ignore what they don't
use); the accepted-and-constrained subset is validated here and in
:mod:`jentic_one.auth.services.oauth_dcr_service`.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

_SCOPE_TOKEN_RE = re.compile(r"^[a-zA-Z0-9_:./-]+$")


class OAuthClientRegistrationRequest(BaseModel):
    """POST /oauth-clients request body (RFC 7591 client metadata subset)."""

    # Bounded to the oauth_clients.name column (String(255)): the endpoint is
    # anonymous and the name flows into event summaries on operator surfaces.
    # Client-claimed and untrusted (phishing) — consent UIs render the
    # redirect-URI origin prominently, never the name alone.
    client_name: str = Field(
        min_length=1,
        max_length=255,
        description="Human-readable client name; self-asserted and not verified by the server.",
    )
    # Native MCP clients are RFC 8252 apps: alongside https (always) and
    # loopback http, §7.1 private-use schemes (e.g. ``cursor://…``,
    # ``com.example.app:/…``) are accepted on this door — PKCE S256 is the
    # compensating control. Dangerous schemes (javascript/data/file/…) are
    # rejected; admin-created clients stay https-or-loopback-http only.
    redirect_uris: list[str] = Field(
        min_length=1,
        max_length=20,
        description="1-20 redirect URIs. `https` always; `http` for loopback "
        "hosts only; RFC 8252 §7.1 private-use (custom) schemes are accepted "
        "for native apps (browser-executable and other dangerous schemes are "
        "rejected).",
    )
    token_endpoint_auth_method: str | None = Field(
        default=None,
        description="Must be 'none' if supplied — this endpoint only registers "
        "public (secret-less, PKCE-only) clients.",
    )
    grant_types: list[str] | None = Field(
        default=None,
        description="Subset of ['authorization_code', 'refresh_token'].",
    )
    response_types: list[str] | None = Field(
        default=None, description="Only ['code'] is supported."
    )
    scope: str | None = Field(
        default=None,
        max_length=6500,
        description=(
            "Space-separated scopes requested as the client's ceiling (at most 100 tokens);"
            " capped to the scopes the server grants OAuth clients, and the full set when omitted."
        ),
    )
    software_id: str | None = Field(
        default=None,
        max_length=255,
        description=(
            "Stable identifier of the client software; with the redirect URIs it is used to"
            " match a repeat registration to the existing client."
        ),
    )
    software_version: str | None = Field(
        default=None, max_length=255, description="Version of the client software."
    )
    application_type: str | None = Field(
        default=None,
        max_length=32,
        description="Accepted and echoed ('native' for desktop/CLI apps, per "
        "the 2026-07-28 MCP spec revision); localhost http redirect URIs are "
        "allowed regardless.",
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


class OAuthClientRegistrationResponse(BaseModel):
    """POST /oauth-clients response (201 created, or 200 on a D8 dedupe hit).

    RFC 7591-compatible: ``client_id`` plus the registered metadata. No
    ``client_secret`` (public clients only) and no
    ``registration_access_token`` (D12 — no RFC 7592 self-management surface;
    clients retry ``/authorize``, they don't poll).
    """

    client_id: str = Field(description="The client identifier to use in OAuth requests.")
    client_id_issued_at: int = Field(
        description="Seconds since the Unix epoch at which the client_id was issued."
    )
    client_name: str = Field(description="The client name as sent in the request.")
    redirect_uris: list[str] = Field(description="The registered redirect URIs.")
    token_endpoint_auth_method: str = Field(
        default="none", description="Always `none`: the client is public and uses PKCE."
    )
    grant_types: list[str] = Field(
        default=["authorization_code", "refresh_token"],
        description="Grant types the client may use.",
    )
    response_types: list[str] = Field(
        default=["code"], description="Response types the client may use; always `code`."
    )
    scope: str = Field(
        description="Space-separated scope ceiling granted to the client "
        "(the request's scope capped to the MCP tool-scope set)."
    )
    software_id: str | None = Field(
        default=None, description="The software ID as sent in the request; omitted if not sent."
    )
    software_version: str | None = Field(
        default=None,
        description="The software version as sent in the request; omitted if not sent.",
    )
    application_type: str | None = Field(
        default=None,
        description="The application type as sent in the request; omitted if not sent.",
    )
