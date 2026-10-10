"""Execution approval service-layer models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from jentic_one.shared.models.execution_approvals import ApprovalDecision


class ExecutionApprovalView(BaseModel):
    """One execution approval row."""

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


class HeldRequestView(BaseModel):
    """The held call as the reviewer sees it — what runs if they approve."""

    method: str
    url: str
    body: str | None = None
    body_truncated: bool = False


class ExecutionApprovalDetailView(ExecutionApprovalView):
    """An approval plus the agent context and the held request."""

    agent_name: str | None = None
    agent_owner_id: str | None = None
    request: HeldRequestView | None = None


class DecideInput(BaseModel):
    """A reviewer's decision."""

    decision: ApprovalDecision
    reason: str | None = None
