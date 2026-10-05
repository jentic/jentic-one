"""Execution approval service-layer view models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ExecutionApprovalView(BaseModel):
    """Internal view model for an execution approval row."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    agent_id: str
    credential_id: str
    api_vendor: str
    api_name: str
    api_version: str
    operation_id: str | None = None
    method: str
    path: str
    matched_rule_id: str | None = None
    state: str
    expires_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None
    decision_reason: str | None = None
    trace_id: str | None = None
    execution_id: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class DecideInput(BaseModel):
    """Input for the approve/deny action."""

    decision: str  # "approved" | "denied"
    reason: str | None = None
