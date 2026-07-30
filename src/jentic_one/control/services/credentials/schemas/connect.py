"""Connect-flow value objects for the credential provider protocol."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ConnectRequest(BaseModel):
    """Inbound connect intent from the caller."""

    scopes: list[str] = Field(default_factory=list)
    extra: dict[str, str] = Field(default_factory=dict)
    # Callback URL the web layer resolved for this request (public_base_url or
    # request origin + the callback path). A provider with an explicitly
    # configured redirect_uri prefers that; otherwise it uses this. None for
    # providers that don't use a redirect (e.g. Pipedream-hosted flows).
    redirect_uri: str | None = None


class ConnectChallenge(BaseModel):
    """Outbound challenge returned by a provider's begin_connect."""

    authorize_url: str
    state: str


class ConnectState(BaseModel):
    """Verified, decoded state payload from the connect flow."""

    credential_id: str
    provider: str
    actor_id: str | None = None
    actor_type: str | None = None
    issued_at: datetime
    nonce: str
    # The exact redirect_uri used in the authorize request, carried across the
    # round trip so the token exchange replays it byte-identically (RFC 6749
    # §4.1.3 requires the match). None for legacy/older flows and non-redirect
    # providers.
    redirect_uri: str | None = None


class ConnectCallback(BaseModel):
    """Provider-agnostic callback inputs received after user authorization."""

    code: str | None = None
    account_id: str | None = None
    error: str | None = None
    raw: dict[str, str] = Field(default_factory=dict)
