"""Provision/refresh result value objects and supporting models."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime

from pydantic import BaseModel, Field

from jentic_one.shared.schemas import APIReference as _BaseAPIReference


class ServiceAPIReference(_BaseAPIReference):
    """Extends the shared APIReference with a host field for provider operations."""

    host: str | None = None


APIReference = ServiceAPIReference


class ProvisionResult(BaseModel):
    """Cleartext tokens and metadata returned by complete_connect."""

    access_token: str | None = None
    refresh_token: str | None = None
    expires_at: datetime | None = None
    scope: str | None = None
    provider_account_ref: str | None = None
    # The token endpoint the code was exchanged at, when the provider has one.
    # The token write re-checks it against the credential's current endpoint
    # (``token_endpoint.minted_at_current_origin``).
    token_url: str | None = None


class RefreshResult(BaseModel):
    """Result of a token refresh operation."""

    access_token: str
    expires_at: datetime | None = None
    refresh_token: str | None = None
    scope: str | None = None


class OAuthTokenView(BaseModel):
    """Redacted view of an oauth_tokens row passed to provider.refresh.

    Cleartext secrets are NOT stored as model fields. The decrypt callable
    is injected by the Broker seam (finalised in M8); M3 defines only the
    field shape.
    """

    model_config = {"arbitrary_types_allowed": True}

    credential_id: str
    provider: str
    provider_account_ref: str | None = None
    expires_at: datetime | None = None
    decrypt: Callable[[], Awaitable[str]] = Field(exclude=True)
