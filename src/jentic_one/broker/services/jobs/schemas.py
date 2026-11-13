"""Service-layer schemas for broker job status and result reads."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class BrokerJobView(BaseModel):
    """Caller-scoped broker execution job representation."""

    id: str
    kind: str
    status: str
    parent_job_id: str | None = None
    execution_id: str | None = None
    error: str | None = None
    result_available: bool = False
    created_at: datetime
    updated_at: datetime | None = None
    actor_id: str | None = None
    actor_type: str


class BrokerJobResultView(BaseModel):
    """Caller-scoped broker execution job result representation."""

    id: str
    job_id: str
    kind: str
    body: dict[str, Any]
    content_type: str | None = None
    raw_body: bytes | None = None
    available_until: datetime | None = None
    created_at: datetime
