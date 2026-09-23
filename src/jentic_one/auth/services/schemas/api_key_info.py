"""API key metadata schema for the agent API-key surfaces."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ApiKeyInfo(BaseModel):
    """Read-model for API key metadata (retrievable even after revocation)."""

    id: str
    status: str
    created_at: datetime
    rotated_at: datetime | None = None
    created_by: str | None = None


class ApiKeyHistoryEntry(BaseModel):
    """A single audit-log event in the API key's lifecycle."""

    id: str
    action: str
    reason: str | None = None
    actor_id: str | None = None
    occurred_at: datetime
