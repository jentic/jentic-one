"""OAuth grant response schemas for the admin web layer."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from jentic_one.shared.web import openapi_examples as ex


class OAuthGrantAdminResponse(BaseModel):
    """One consent→agent grant in the admin cross-view."""

    id: str = Field(description="Grant ID (ksuid, `ocg_` prefix).", examples=[ex.OAUTH_GRANT_ID])
    oauth_client_id: str = Field(
        description="The client's public client_id — the same identifier stamped "
        "on tokens minted under this grant.",
        examples=[ex.OAUTH_PUBLIC_CLIENT_ID],
    )
    client_name: str | None = Field(
        description="Display name of the registered client, if the row still exists.",
        examples=["billing-assistant-prod"],
    )
    client_origin: str | None = Field(
        description="Origin (scheme://host) of the client's first redirect URI — "
        "the 'authorized apps' display pattern.",
        examples=["https://client.example.com"],
    )
    user_id: str = Field(
        description="The consenting user who approved this grant. Shown even "
        "after an agent ownership transfer: the grant stays with the original "
        "consenter (it is their consent, not the agent's).",
        examples=[ex.USER_ID],
    )
    agent_id: str = Field(
        description="The agent this grant binds the client to.", examples=[ex.AGENT_ID]
    )
    agent_status: str | None = Field(
        default=None,
        description="Lifecycle state of the bound agent (`active`, `disabled`, "
        "`archived`, …). A grant on a non-active agent is dormant: the row "
        "stays `active` (disable is reversible — re-enable restores the "
        "standing consent without a new consent round) but no token resolves "
        "while the agent is non-active. Lets listings tell a working "
        "connection from a dormant one (#1233).",
        examples=["active"],
    )
    scopes: list[str] = Field(
        description="Scopes granted at consent (the D2 intersection).",
        examples=[["apis:read", "capabilities:execute"]],
    )
    status: str = Field(
        description="Grant lifecycle state: ``active`` or ``revoked``.", examples=["revoked"]
    )
    created_at: datetime = Field(
        description="When the grant was created (RFC 3339).", examples=["2026-01-15T09:30:00Z"]
    )
    revoked_at: datetime | None = Field(
        description="When the grant was revoked (RFC 3339); null while it is active.",
        examples=["2026-01-20T14:05:00Z"],
    )
    last_used_at: datetime | None = Field(
        description="Last time the client obtained tokens under this grant "
        "(stamped at exchange/refresh, not per request).",
        examples=["2026-01-19T08:12:00Z"],
    )
    # G10: the list gate (`oauth-clients:read`) is wider than the revoke set
    # (org:admin / oauth-clients:write) — a read-only admin can list but not
    # revoke. `can_revoke` surfaces the revoke predicate per item so clients
    # never render a revoke that would 403.
    can_revoke: bool = Field(
        description="Whether the CALLER may revoke this grant (the consenting "
        "user, or an admin holding the revoke permission set). May be false "
        "even for callers who can list — e.g. a read-only admin.",
        examples=[True],
    )


class OAuthGrantAdminListResponse(BaseModel):
    """A paginated list of OAuth grants (admin cross-view)."""

    data: list[OAuthGrantAdminResponse] = Field(description="The page of grants.")
    has_more: bool = Field(
        description="Whether more results exist after this page.", examples=[True]
    )
    next_cursor: str | None = Field(
        default=None,
        description="Cursor for the next page; null when there are no more results.",
        examples=[ex.CURSOR],
    )
