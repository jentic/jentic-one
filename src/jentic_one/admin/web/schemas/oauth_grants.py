"""OAuth grant response schemas for the admin web layer."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class OAuthGrantAdminResponse(BaseModel):
    """One consent→agent grant in the admin cross-view."""

    id: str = Field(description="Grant ID (ksuid, `ocg_` prefix).")
    oauth_client_id: str = Field(
        description="The client's public client_id — the same identifier stamped "
        "on tokens minted under this grant."
    )
    client_name: str | None = Field(
        description="Display name of the registered client, if the row still exists."
    )
    client_origin: str | None = Field(
        description="Origin (scheme://host) of the client's first redirect URI — "
        "the 'authorized apps' display pattern."
    )
    user_id: str = Field(
        description="The consenting user who approved this grant. Shown even "
        "after an agent ownership transfer: the grant stays with the original "
        "consenter (it is their consent, not the agent's)."
    )
    agent_id: str = Field(description="The agent this grant binds the client to.")
    scopes: list[str] = Field(description="Scopes granted at consent (the D2 intersection).")
    status: str = Field(description="Grant lifecycle state: ``active`` or ``revoked``.")
    created_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None = Field(
        description="Last time the client obtained tokens under this grant "
        "(stamped at exchange/refresh, not per request)."
    )
    # G10: the list gate (`oauth-clients:read`) is wider than the revoke set
    # (org:admin / oauth-clients:write) — a read-only admin can list but not
    # revoke. `can_revoke` surfaces the revoke predicate per item so clients
    # never render a revoke that would 403.
    can_revoke: bool = Field(
        description="Whether the CALLER may revoke this grant (the consenting "
        "user, or an admin holding the revoke permission set). May be false "
        "even for callers who can list — e.g. a read-only admin."
    )


class OAuthGrantAdminListResponse(BaseModel):
    """A paginated list of OAuth grants (admin cross-view)."""

    data: list[OAuthGrantAdminResponse]
    has_more: bool
    next_cursor: str | None = None
