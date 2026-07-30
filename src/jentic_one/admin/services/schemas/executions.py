"""Execution record schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ApiInfo(BaseModel):
    """Reconstructed API identification."""

    vendor: str
    name: str
    version: str
    host: str | None = None


class ExecutionView(BaseModel):
    """Public execution record representation."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    toolkit_id: str
    toolkit_name: str | None = None
    trace_id: str
    started_at: datetime
    duration_ms: int | None = None
    status: str
    operation_id: str | None = None
    api: ApiInfo | None = None
    pinned_revisions: dict[str, Any] | None = None
    http_status: int | None = None
    error: str | None = None
    created_at: datetime
    actor_id: str
    actor_type: str
    origin: str | None = None
    credential_id: str | None = None
    credential_name: str | None = None


class ExecutionFilter(BaseModel):
    """Filter parameters for listing executions."""

    toolkit_id: str | None = None
    trace_id: str | None = None
    status: list[str] | None = None
    from_: datetime | None = None
    to: datetime | None = None
    api_vendor: str | None = None
    api_name: str | None = None
    api_version: str | None = None
    actor_id: str | None = None
    origin: str | None = None
