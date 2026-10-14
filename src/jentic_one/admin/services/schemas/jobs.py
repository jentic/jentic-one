"""Job schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class JobView(BaseModel):
    """Public job representation."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    status: str
    parent_job_id: str | None = None
    execution_id: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class JobFilter(BaseModel):
    """Filter parameters for listing jobs."""

    kind: str | None = None
    status: list[str] | None = None
    from_: datetime | None = None
    to: datetime | None = None


class JobResultView(BaseModel):
    """Public job result representation."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    kind: str
    body: dict[str, Any]
    content_type: str | None = None
    raw_body: bytes | None = None
    available_until: datetime | None = None
    created_at: datetime
