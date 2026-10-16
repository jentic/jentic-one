"""Request/response schemas for the Overlays endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from jentic_one.shared.web import openapi_examples as ex

# Example-only values shared by several fields below. The ``OverlayResponse``
# field examples describe one coherent lifecycle: an overlay written against
# ``REVISION_ID``, confirmed (materializing ``NEXT_REVISION_ID``), then rolled back.
_EX_OVERLAY_DOCUMENT: dict[str, Any] = {
    "overlay": "1.0.0",
    "info": {"title": "Clarify charges listing", "version": "1.0.0"},
    "actions": [
        {
            "target": "$.paths['/v1/charges'].get",
            "update": {"summary": "List all charges, newest first"},
        }
    ],
}
_EX_API_URL = f"{ex.BASE_URL}/apis/{ex.API_VENDOR}/{ex.API_NAME}/{ex.API_VERSION}"
_EX_OVERLAY_URL = f"{_EX_API_URL}/overlays/{ex.OVERLAY_ID}"
_EX_CONTRIBUTED_BY = "docs-review-agent"


class OverlaySubmitRequest(BaseModel):
    """Payload for submitting a new overlay."""

    model_config = ConfigDict(extra="forbid")

    document: dict[str, Any] = Field(
        description="OpenAPI Overlay document; must be a JSON object with an `actions` array.",
        examples=[_EX_OVERLAY_DOCUMENT],
    )
    target_revision_id: str | None = Field(
        default=None,
        description="ID of the revision the overlay was written against; null to leave unpinned.",
        examples=[ex.REVISION_ID],
    )
    contributed_by: str | None = Field(
        default=None,
        description="Free-text attribution label for who contributed the overlay.",
        examples=[_EX_CONTRIBUTED_BY],
    )


class OverlayUpdateRequest(BaseModel):
    """Payload for updating an existing overlay."""

    model_config = ConfigDict(extra="forbid")

    document: dict[str, Any] | None = Field(
        default=None,
        description="Replacement OpenAPI Overlay document; omit to keep the current one.",
        examples=[_EX_OVERLAY_DOCUMENT],
    )
    target_revision_id: str | None = Field(
        default=None,
        description="ID of the revision the overlay was written against.",
        examples=[ex.REVISION_ID],
    )


class OverlayConfirmRequest(BaseModel):
    """Payload for confirming an overlay."""

    model_config = ConfigDict(extra="forbid")

    execution_id: str | None = Field(
        default=None,
        description=(
            "ID of the execution that verified the overlay, recorded as "
            "`confirmed_by_execution_id`."
        ),
        examples=[ex.EXECUTION_ID],
    )


class OverlayLinksResponse(BaseModel):
    """Hypermedia links for an overlay resource.

    Links are advertised based on the overlay's *state validity* (mirroring the
    revisions resource's promote/archive links), so a surface renders an action only
    when it is applicable to the current status. They are not permission-scoped — the
    ``overlays:confirm`` gate on confirm/rollback is still enforced server-side (403).
    """

    model_config = ConfigDict(populate_by_name=True)

    self_link: str = Field(
        serialization_alias="self", description="URL of this overlay.", examples=[_EX_OVERLAY_URL]
    )
    api: str = Field(description="URL of the API the overlay applies to.", examples=[_EX_API_URL])
    #: Present only while PENDING (the only state confirm is valid).
    confirm: str | None = Field(
        default=None,
        description="URL to confirm the overlay; null unless it is `pending`.",
        examples=[f"{_EX_OVERLAY_URL}:confirm"],
    )
    #: Present only while materialized (CONFIRMED with a confirmed_revision_id): the
    #: state where rolling back to the superseded revision is meaningful.
    rollback: str | None = Field(
        default=None,
        description=(
            "URL to roll back the overlay; null unless it is `confirmed` and its revision "
            "has been produced."
        ),
        examples=[f"{_EX_OVERLAY_URL}:rollback"],
    )
    #: Present unless already DEPRECATED (PENDING or CONFIRMED can be deprecated).
    deprecate: str | None = Field(
        default=None,
        description="URL to deprecate the overlay (via `DELETE`); null once it is `deprecated`.",
        examples=[_EX_OVERLAY_URL],
    )


class OverlayResponse(BaseModel):
    """Full overlay resource response."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(description="Unique ID of the overlay.", examples=[ex.OVERLAY_ID])
    api_id: str = Field(
        description="ID of the API the overlay applies to.", examples=[ex.REGISTRY_API_ID]
    )
    status: str = Field(
        description="Lifecycle status of the overlay: `pending`, `confirmed` or `deprecated`.",
        examples=["deprecated"],
    )
    document: dict[str, Any] = Field(
        description="The OpenAPI Overlay document.", examples=[_EX_OVERLAY_DOCUMENT]
    )
    target_revision_id: str | None = Field(
        description="ID of the revision the overlay was written against; null if unpinned.",
        examples=[ex.REVISION_ID],
    )
    confirmed_revision_id: str | None = Field(
        default=None,
        description=(
            "ID of the revision produced by applying the overlay; null until confirmation "
            "completes."
        ),
        examples=[ex.NEXT_REVISION_ID],
    )
    #: The revision this overlay superseded when it materialized (its rollback
    #: target) — lets a client tell "rolled back" (the superseded revision is
    #: serving again) from "deprecated".
    superseded_revision_id: str | None = Field(
        default=None,
        description=(
            "ID of the revision the overlay replaced when applied, restored by a rollback; "
            "null if none."
        ),
        examples=[ex.REVISION_ID],
    )
    contributed_by: str | None = Field(
        description="Free-text attribution label supplied at submission; null if none.",
        examples=[_EX_CONTRIBUTED_BY],
    )
    #: The authenticated principal that submitted the overlay (``identity.sub``).
    #: Distinct from the free-text ``contributed_by`` attribution in the submit body.
    created_by: str | None = Field(
        default=None,
        description="ID of the actor that created the overlay.",
        examples=[ex.AGENT_ID],
    )
    confirmed_by_execution_id: str | None = Field(
        description="ID of the execution supplied when the overlay was confirmed; null if none.",
        examples=[ex.EXECUTION_ID],
    )
    created_at: datetime = Field(
        description="When the overlay was created (RFC 3339).", examples=["2026-01-15T09:30:00Z"]
    )
    updated_at: datetime | None = Field(
        description="When the overlay was last updated (RFC 3339); null if never updated.",
        examples=["2026-01-17T16:45:00Z"],
    )
    confirmed_at: datetime | None = Field(
        description="When the overlay was confirmed (RFC 3339); null if not confirmed.",
        examples=["2026-01-15T10:05:00Z"],
    )
    deprecated_at: datetime | None = Field(
        description="When the overlay was deprecated (RFC 3339); null if not deprecated.",
        examples=["2026-01-17T16:45:00Z"],
    )
    #: Why the overlay was deprecated: ``manual`` (operator deprecate), ``rollback``
    #: (un-confirm restored the superseded revision), or ``superseded_by_reimport``
    #: (an authorized catalog re-import replaced it). ``null`` when not deprecated,
    #: or for rows deprecated before this field existed.
    deprecated_reason: str | None = Field(
        default=None,
        description=(
            "Why the overlay was deprecated: `manual`, `rollback` or "
            "`superseded_by_reimport`; null when not deprecated or not recorded."
        ),
        examples=["rollback"],
    )
    links: OverlayLinksResponse = Field(serialization_alias="_links")


class OverlayListResponse(BaseModel):
    """Cursor-paginated list of overlays."""

    data: list[OverlayResponse] = Field(description="The page of overlays.")
    has_more: bool = Field(
        description="Whether more results exist after this page.", examples=[True]
    )
    next_cursor: str | None = Field(
        default=None,
        description="Cursor for the next page; null when there are no more results.",
        examples=[ex.CURSOR],
    )
