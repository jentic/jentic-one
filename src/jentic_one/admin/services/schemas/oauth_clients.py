"""Service-layer views for OAuth client management."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class OAuthClientView(BaseModel):
    """A view of a registered OAuth client returned by the service."""

    id: str
    client_id: str
    name: str
    description: str | None
    redirect_uris: list[str]
    allowed_scopes: list[str] | None
    active: bool
    require_consent: bool
    token_endpoint_auth_method: str
    consent_model: str
    registration_source: str
    software_id: str | None
    approval_status: str
    active_grant_count: int = 0
    created_at: datetime
    updated_at: datetime | None
    created_by: str | None


class OAuthClientCreateResult(OAuthClientView):
    """Returned on creation — includes the one-time plaintext client secret.

    ``client_secret`` is None for public clients (``token_endpoint_auth_method
    = none``), which have no secret at all.
    """

    client_secret: str | None
