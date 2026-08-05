"""Ingest domain models — specification identifier and content."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from jentic_one.shared.models import ApiRevisionSourceType


class SpecType(StrEnum):
    """Supported specification types for ingestion."""

    OPENAPI = "openapi"


class ApiIdentifier(BaseModel):
    """Uniquely identifies an API by vendor, name, and version."""

    vendor: str
    name: str
    version: str
    filename: str = "openapi.json"

    @property
    def api_name(self) -> str:
        return f"{self.vendor}/{self.name}"

    def __str__(self) -> str:
        return f"{self.vendor}/{self.name}/{self.version}"


class IngestSpecification(BaseModel):
    """A specification document submitted for ingestion."""

    spec_type: SpecType
    api_identifier: ApiIdentifier
    sha: str | None = None
    metadata: dict[str, Any] | None = None
    content: dict[str, Any] | None = None
    source_id: str | None = None
    source_type: ApiRevisionSourceType | None = None
    source_url: str | None = None
    source_filename: str | None = None
    submitted_by: str | None = None
    origin: str | None = None
    # Catalog identity slug (`domain[/sub-api]`) for catalog-originated imports;
    # None for manual/inline sources. Persisted verbatim on the Api row so
    # display surfaces can derive friendly titles without reverse-engineering
    # the slugified vendor/name tuple.
    catalog_api_id: str | None = None
    #: Overlay-only: the base revision's ``spec_digest`` this overlay was materialized
    #: over (distinct from ``sha``, which is the overlaid digest). Persisted on the
    #: resulting ``api_revisions`` row so the Flow-3 sweep can diff upstream against the
    #: overlay's base. NULL for non-overlay ingests.
    overlay_base_digest: str | None = None
    #: Authorized-supersede flag (A4b): when a catalog re-import is allowed to replace a
    #: live confirmed overlay, the current revision may be overlay-origin (not catalog),
    #: so the stage must archive *every* active revision rather than only the same-origin
    #: one. Set only by the scope-checked enqueue path; ``False`` for ordinary imports.
    supersede_active: bool = False
    #: Overlay-only: the id of the overlay this materialize job is (re-)materializing. Lets
    #: ``CreateRevisionStage`` tell a *re-materialize of the same overlay* (skip re-capturing
    #: ``superseded_revision_id`` so the overlay keeps its original clean base for rollback,
    #: D1) apart from a *stacked confirm of a different overlay* over a live overlay's output
    #: (which must capture the current revision as the new overlay's superseded target). NULL
    #: for non-overlay ingests. Server-set (never client-controlled), like ``supersede_active``.
    overlay_id: str | None = None

    def to_log_string(self) -> str:
        fields = self.model_dump(exclude={"content"})
        return " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
