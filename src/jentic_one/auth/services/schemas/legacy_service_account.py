"""Service-layer view for the retired service-account self-identity read (theme 8)."""

from __future__ import annotations

from pydantic import BaseModel


class LegacyServiceAccountIdentityView(BaseModel):
    """What ``GET /me`` reports for a fallback-resolved ``sva_`` caller."""

    id: str
    name: str
    status: str
    registered_by: str
    approved_by: str | None = None
    scopes: list[str]
