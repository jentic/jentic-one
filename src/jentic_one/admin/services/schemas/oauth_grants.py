"""Service-layer views for OAuth consent→agent grants (phase-3a §4.8)."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse

from pydantic import BaseModel

from jentic_one.admin.core.schema.oauth_client_grants import OAuthClientGrant
from jentic_one.admin.core.schema.oauth_clients import OAuthClient


class OAuthGrantView(BaseModel):
    """One consent→agent grant row, enriched with client display fields.

    ``user_id`` is the consenting owner — surfaced deliberately (gap G10:
    agent ownership transfer strands the grant with the old owner, so
    listings must show WHO consented, not just which agent is bound).
    ``can_revoke`` is the viewer's per-item ``:revoke`` capability — computed
    from the same predicate the revoke endpoint enforces, so the UI never
    advertises a revoke that would 403 (the G10 list/revoke divergence).
    """

    id: str
    oauth_client_id: str
    client_name: str | None
    client_origin: str | None
    user_id: str
    agent_id: str
    scopes: list[str]
    status: str
    created_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None
    can_revoke: bool


def redirect_uri_origin(client: OAuthClient | None) -> str | None:
    """The client's first redirect-URI origin (§4.8 "authorized apps" pattern)."""
    if client is None or not client.redirect_uris:
        return None
    parsed = urlparse(client.redirect_uris[0])
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def grant_to_view(
    grant: OAuthClientGrant, client: OAuthClient | None, *, can_revoke: bool
) -> OAuthGrantView:
    """Build the enriched view; ``client`` may be None for a deleted row."""
    return OAuthGrantView(
        id=grant.id,
        oauth_client_id=grant.oauth_client_id,
        client_name=client.name if client is not None else None,
        client_origin=redirect_uri_origin(client),
        user_id=grant.user_id,
        agent_id=grant.agent_id,
        scopes=list(grant.scopes),
        status=grant.status,
        created_at=grant.created_at,
        revoked_at=grant.revoked_at,
        last_used_at=grant.last_used_at,
        can_revoke=can_revoke,
    )
