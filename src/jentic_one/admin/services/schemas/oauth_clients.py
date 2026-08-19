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
    active: bool
    require_consent: bool
    created_at: datetime
    updated_at: datetime | None
    created_by: str | None
