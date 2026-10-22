"""CLI invocation token request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from jentic_one.shared.web import openapi_examples as ex
from jentic_one.shared.web.sensitive import SENSITIVE

_EX_TOKEN = "cit_example-invocation-token"


class CliInvocationCreateRequest(BaseModel):
    """Request body for minting a CLI invocation token."""

    model_config = ConfigDict(extra="forbid")

    vendor: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[a-z0-9-]+$",
        description="Vendor whose CLI facade the token is for: `github`, `cloudflare` or `aws`.",
        examples=["github"],
    )
    credential_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description=(
            "Name of the credential to use when the agent has several for this vendor "
            "(the wrapper's `JENTIC_CREDENTIAL`). The Broker applies it to every call the "
            "token makes, exactly as the `Jentic-Credential-Name` header; omit it to let "
            "the Broker select the credential as for any call. Not checked at mint: a name "
            "that matches none of the agent's credentials fails each call instead."
        ),
        examples=["GitHub (acme org)"],
    )
    command_title: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        # Space-separated words that cannot start with ``-``, so a flag (and
        # with it most argument values) cannot ride along.
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*( [A-Za-z0-9][A-Za-z0-9._:-]*)*$",
        description=(
            "The command's subcommand path as reported by the agent, e.g. `pr create`; "
            "never its arguments (words starting with `-` are rejected). Display-only."
        ),
        examples=["pr create"],
    )


class CliInvocationResponse(BaseModel):
    """A CLI invocation token's record (never the token itself)."""

    id: str = Field(description="Unique ID of the invocation.", examples=[ex.CLI_INVOCATION_ID])
    vendor: str = Field(description="Vendor the token is for.", examples=["github"])
    credential_name: str | None = Field(
        default=None,
        description=(
            "Name of the credential the token's calls use; null when the Broker selects it."
        ),
        examples=["GitHub (acme org)"],
    )
    trace_id: str = Field(
        description=(
            "W3C trace ID chosen by the server; every call the token makes is recorded under it."
        ),
        examples=[ex.TRACE_ID],
    )
    command_title: str | None = Field(
        default=None,
        description="Agent-reported subcommand path; null if not given.",
        examples=["pr create"],
    )
    expires_at: datetime = Field(
        description="When the token stops working unless renewed (RFC 3339).",
        examples=["2026-01-15T09:40:00Z"],
    )
    hard_expires_at: datetime = Field(
        description="When the token stops working regardless of renewals (RFC 3339).",
        examples=["2026-01-15T10:30:00Z"],
    )
    revoked_at: datetime | None = Field(
        default=None,
        description="When the token was revoked (RFC 3339); null while live.",
        examples=[None],
    )


class CliInvocationCreatedResponse(CliInvocationResponse):
    """A freshly minted CLI invocation, including its token (shown once)."""

    token: str = Field(
        json_schema_extra=SENSITIVE,
        description=(
            "The invocation token (`cit_…`). Shown once; accepted only by the Broker's "
            "CLI facade for `vendor`."
        ),
        examples=[_EX_TOKEN],
    )
