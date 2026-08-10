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
    """Hypermedia links for an overlay resource.

    Links are advertised based on the overlay's *state validity* (mirroring the
    revisions resource's promote/archive links), so a surface renders an action only
    when it is applicable to the current status. They are not permission-scoped — the
    ``overlays:confirm`` gate on confirm/rollback is still enforced server-side (403).
    """

    model_config = ConfigDict(populate_by_name=True)

    self_link: str = Field(serialization_alias="self")
    api: str
    #: Present only while PENDING (the only state confirm is valid).
    confirm: str | None = None
    #: Present only while materialized (CONFIRMED with a confirmed_revision_id): the
    #: state where rolling back to the superseded revision is meaningful.
    rollback: str | None = None
    #: Present unless already DEPRECATED (PENDING or CONFIRMED can be deprecated).
    deprecate: str | None = None


class OverlayResponse(BaseModel):
    """Full overlay resource response."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    api_id: str
    status: str
    document: dict[str, Any]
    target_revision_id: str | None
    confirmed_revision_id: str | None = None
    #: The revision this overlay superseded when it materialized (its rollback
    #: target) — lets a client tell "rolled back" (the superseded revision is
    #: serving again) from "deprecated".
    superseded_revision_id: str | None = None
    contributed_by: str | None
    #: The authenticated principal that submitted the overlay (``identity.sub``).
    #: Distinct from the free-text ``contributed_by`` attribution in the submit body.
    created_by: str | None = None
    confirmed_by_execution_id: str | None
    created_at: datetime
    updated_at: datetime | None
    confirmed_at: datetime | None
    deprecated_at: datetime | None
    #: Why the overlay was deprecated: ``manual`` (operator deprecate), ``rollback``
    #: (un-confirm restored the superseded revision), or ``superseded_by_reimport``
    #: (an authorized catalog re-import replaced it). ``null`` when not deprecated,
    #: or for rows deprecated before this field existed.
    deprecated_reason: str | None = None
    links: OverlayLinksResponse = Field(serialization_alias="_links")


class OverlayListResponse(BaseModel):
    """Cursor-paginated list of overlays."""

    data: list[OverlayResponse]
    has_more: bool
    next_cursor: str | None = None
