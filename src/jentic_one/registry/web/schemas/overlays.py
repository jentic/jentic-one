"""Request/response schemas for the Overlays endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OverlaySubmitRequest(BaseModel):
    """Payload for submitting a new overlay."""

    model_config = ConfigDict(extra="forbid")

    document: dict[str, Any]
    target_revision_id: str | None = None
    contributed_by: str | None = None


class OverlayUpdateRequest(BaseModel):
    """Payload for updating an existing overlay."""

    model_config = ConfigDict(extra="forbid")

    document: dict[str, Any] | None = None
    target_revision_id: str | None = None


class OverlayConfirmRequest(BaseModel):
    """Payload for confirming an overlay."""

    model_config = ConfigDict(extra="forbid")

    execution_id: str | None = None


class OverlayLinksResponse(BaseModel):
    """Hypermedia links for an overlay resource."""

    model_config = ConfigDict(populate_by_name=True)

    self_link: str = Field(serialization_alias="self")
    api: str
    confirm: str | None = None


class OverlayResponse(BaseModel):
    """Full overlay resource response."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    api_id: str
    status: str
    document: dict[str, Any]
    target_revision_id: str | None
    confirmed_revision_id: str | None = None
    contributed_by: str | None
    confirmed_by_execution_id: str | None
    created_at: datetime
    updated_at: datetime | None
    confirmed_at: datetime | None
    deprecated_at: datetime | None
    links: OverlayLinksResponse = Field(serialization_alias="_links")


class OverlayListResponse(BaseModel):
    """Cursor-paginated list of overlays."""

    data: list[OverlayResponse]
    has_more: bool
    next_cursor: str | None = None
