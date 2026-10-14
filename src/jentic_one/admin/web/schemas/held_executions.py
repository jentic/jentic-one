"""Held execution request/response schemas — calls parked for a human decision."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jentic_one.shared.models.held_executions import ApprovalDecision, HeldExecutionState

#: Where a held call stands, from held through to its outcome.
HeldExecutionStatus = Literal["held", "queued", "running", "completed", "failed", "cancelled"]


class HeldExecutionLinksResponse(BaseModel):
    """HAL-style links for a held execution."""

    self_link: str = Field(serialization_alias="self", description="This held execution.")
    result: str | None = Field(
        default=None,
        description="The call's result: the upstream response, or why it never ran. "
        "Present once the call has finished.",
    )
    withdraw: str | None = Field(
        default=None, description="Withdraw the held call. Present while it is pending."
    )
    execution: str | None = Field(
        default=None, description="The execution record, once the approved call has run."
    )


class HeldExecutionResponse(BaseModel):
    """A call held for a human decision, and where it stands."""

    id: str = Field(description="Held execution id (`held_…`).")
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
    state: HeldExecutionState = Field(
        description="The decision: `pending` until a reviewer acts, then `approved`, "
        "`denied`, `expired` or `withdrawn`."
    )
    execution_status: HeldExecutionStatus | None = Field(
        default=None,
        description="Where the held call stands: `held` until decided, then `queued`, "
        "`running`, and `completed`, `failed` or `cancelled`.",
    )
    expires_at: datetime = Field(description="When a pending held execution expires undecided.")
    review_url: str = Field(
        description="Review page a signed-in reviewer opens to approve or deny the call. "
        "Carries no credential."
    )
    decided_at: datetime | None = Field(default=None, description="When it left `pending`.")
    decided_by: str | None = Field(default=None, description="Reviewer who decided it.")
    decision_reason: str | None = Field(default=None, description="Reviewer's reason.")
    trace_id: str | None = Field(default=None, description="Trace id of the held call.")
    execution_id: str | None = Field(
        default=None, description="Execution record written once the approved call ran."
    )
    created_at: datetime = Field(description="When the call was held.")
    updated_at: datetime | None = Field(default=None, description="Last change.")
    links: HeldExecutionLinksResponse = Field(serialization_alias="_links")


class HeldRequestResponse(BaseModel):
    """The held call exactly as it runs if approved (credentials are injected at run time)."""

    method: str = Field(description="HTTP method.")
    url: str = Field(description="Upstream URL including the query string.")
    body: str | None = Field(default=None, description="Request body, if any.")
    body_truncated: bool = Field(
        default=False, description="True when `body` is cut short for display."
    )


class HeldExecutionDetailResponse(HeldExecutionResponse):
    """A held execution with the agent context and the held request a reviewer decides on."""

    agent_name: str | None = Field(default=None, description="Display name of the agent.")
    agent_owner_id: str | None = Field(
        default=None, description="The agent's owner; null for an ownerless agent (admin-only)."
    )
    request: HeldRequestResponse | None = Field(default=None, description="The held call.")


class HeldExecutionListResponse(BaseModel):
    """A page of held executions."""

    data: list[HeldExecutionResponse]
    has_more: bool
    next_cursor: str | None = None


class DecideHeldExecutionRequest(BaseModel):
    """Approve or deny a pending held execution."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"decision": "deny", "reason": "Not this account"}]},
    )

    decision: ApprovalDecision = Field(description="`approve` releases the call; `deny` fails it.")
    reason: str | None = Field(
        default=None, max_length=500, description="Optional reason, recorded with the decision."
    )
