"""Service input models for registry note operations."""

from __future__ import annotations

from pydantic import BaseModel

from jentic_one.shared.schemas.api_reference import APIReference


class NoteResource(BaseModel):
    """Target resource identifier for a note."""

    api: APIReference | None = None
    operation_id: str | None = None
    execution_id: str | None = None
    credential_id: str | None = None


class NoteCreate(BaseModel):
    """Input payload for creating a note."""

    resource: NoteResource
    type: str | None = None
    body: str
    confidence: str | None = None
    confidence_source: str = "client"
    source: str | None = None
    related_execution_id: str | None = None


class NoteUpdate(BaseModel):
    """Input payload for partially updating a note."""

    body: str | None = None
    type: str | None = None
    confidence: str | None = None
    source: str | None = None
    related_execution_id: str | None = None


class NoteQuery(BaseModel):
    """Filter and pagination parameters for listing notes."""

    limit: int = 50
    cursor: str | None = None
    api: str | None = None
    operation_id: str | None = None
    execution_id: str | None = None
    credential_id: str | None = None
    type: str | None = None
    created_by: str | None = None
