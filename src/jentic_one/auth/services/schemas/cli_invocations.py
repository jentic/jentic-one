"""Service-layer schemas for vendor CLI invocation tokens."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class CliInvocationMintPayload(BaseModel):
    """What the CLI wrapper asks for when it mints an invocation token."""

    vendor: str
    credential_name: str | None = None
    command_title: str | None = None


class CliInvocationView(BaseModel):
    """Read-model of one invocation (never carries the token)."""

    id: str
    vendor: str
    credential_name: str | None = None
    trace_id: str
    command_title: str | None = None
    expires_at: datetime
    hard_expires_at: datetime
    revoked_at: datetime | None = None


class MintedCliInvocation(BaseModel):
    """A freshly minted invocation: its view plus the plaintext token, shown once."""

    invocation: CliInvocationView
    token: str
