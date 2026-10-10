"""Execution approval request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from jentic_one.shared.models.execution_approvals import ApprovalDecision, ExecutionApprovalState


class ExecutionApprovalLinksResponse(BaseModel):
    """HAL-style links for an execution approval."""

    self_link: str = Field(serialization_alias="self", description="This approval.")
    job: str = Field(description="The held execution job to poll for the outcome.")


class ExecutionApprovalResponse(BaseModel):
    """A held execution waiting on (or settled by) a human decision."""

    id: str = Field(description="Approval id (`exap_…`).")
    job_id: str = Field(description="The held execution job.")
    agent_id: str = Field(description="The agent whose call was held.")
    credential_id: str = Field(description="The credential selected when the call was held.")
    api_vendor: str = Field(description="Vendor of the API called.")
    api_name: str = Field(description="Name of the API called.")
    api_version: str = Field(description="Version of the API called.")
    operation_id: str | None = Field(default=None, description="Resolved OpenAPI operation id.")
    method: str = Field(description="HTTP method of the held call.")
    path: str = Field(description="Upstream path of the held call.")
    matched_rule_id: str | None = Field(
        default=None, description="The require-approval permission rule that held the call."
    )
    state: ExecutionApprovalState = Field(description="Approval state.")
    expires_at: datetime = Field(description="When a pending approval expires undecided.")
    decided_at: datetime | None = Field(default=None, description="When it left `pending`.")
    decided_by: str | None = Field(default=None, description="Reviewer who decided it.")
    decision_reason: str | None = Field(default=None, description="Reviewer's reason.")
    trace_id: str | None = Field(default=None, description="Trace id of the held call.")
    execution_id: str | None = Field(
        default=None, description="Execution record written once the approved job ran."
    )
    created_at: datetime = Field(description="When the call was held.")
    updated_at: datetime | None = Field(default=None, description="Last change.")
    links: ExecutionApprovalLinksResponse = Field(serialization_alias="_links")


class HeldRequestResponse(BaseModel):
    """The held call exactly as it runs if approved (credentials are injected at run time)."""

    method: str = Field(description="HTTP method.")
    url: str = Field(description="Upstream URL including the query string.")
    body: str | None = Field(default=None, description="Request body, if any.")
    body_truncated: bool = Field(
        default=False, description="True when `body` is cut short for display."
    )


class ExecutionApprovalDetailResponse(ExecutionApprovalResponse):
    """An approval with the agent context and the held request a reviewer decides on."""

    agent_name: str | None = Field(default=None, description="Display name of the agent.")
    agent_owner_id: str | None = Field(
        default=None, description="The agent's owner; null for an ownerless agent (admin-only)."
    )
    request: HeldRequestResponse | None = Field(default=None, description="The held call.")


class ExecutionApprovalListResponse(BaseModel):
    """A page of execution approvals."""

    data: list[ExecutionApprovalResponse]
    has_more: bool
    next_cursor: str | None = None


class DecideRequest(BaseModel):
    """Approve or deny a pending execution approval."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"decision": "deny", "reason": "Not this account"}]},
    )

    decision: ApprovalDecision = Field(description="`approve` releases the call; `deny` fails it.")
    reason: str | None = Field(
        default=None, max_length=500, description="Optional reason, recorded with the decision."
    )
