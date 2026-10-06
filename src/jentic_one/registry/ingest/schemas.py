"""Ingest result schemas."""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from jentic_one.shared.models import ApiRevisionState


class HeldHostChange(BaseModel):
    """A catalog revision kept as a draft because it changes the API's server hosts."""

    current_hosts: list[str]
    new_hosts: list[str]


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
    #: Set when the revision was held as a DRAFT for operator review because it
    #: would change the server hosts of an API with bound credentials.
    held_host_change: HeldHostChange | None = None
