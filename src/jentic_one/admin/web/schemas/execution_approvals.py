"""Execution approval request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ExecutionApprovalLinksResponse(BaseModel):
    """HAL-style links for an execution approval."""

    self_link: str = Field(serialization_alias="self")
    job: str | None = None


class ExecutionApprovalResponse(BaseModel):
    """Execution approval detail in API responses."""

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
    links: ExecutionApprovalLinksResponse = Field(serialization_alias="_links")


class ExecutionApprovalListResponse(BaseModel):
    """Paginated list of execution approvals."""

    data: list[ExecutionApprovalResponse]
    has_more: bool
    next_cursor: str | None = None


class DecideRequest(BaseModel):
    """Request body for approving or denying a held execution."""

    decision: str
    reason: str | None = None
