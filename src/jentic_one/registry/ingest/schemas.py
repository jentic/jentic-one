"""Ingest result schemas."""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from jentic_one.shared.models import ApiRevisionState


class IngestResult(BaseModel):
    """Result of a successful ingest operation."""

    api_vendor: str
    api_name: str
    api_version: str
    revision_id: uuid.UUID
    #: The revision this ingest superseded (the API's current revision before an
    #: overlay materialization archived it). Only set for overlay-origin ingests that
    #: replaced an existing current revision; ``None`` otherwise (drafts, catalog
    #: imports, or a first-ever materialize with no prior current revision).
    superseded_revision_id: uuid.UUID | None = None
    state: ApiRevisionState = ApiRevisionState.DRAFT
    operation_count: int
