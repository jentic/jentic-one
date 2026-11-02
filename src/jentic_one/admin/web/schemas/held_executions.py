"""Held execution request/response schemas — calls parked for a human decision."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from jentic_one.shared.models.executions import ExecutionStatus
from jentic_one.shared.models.held_executions import ApprovalDecision, HeldExecutionState
from jentic_one.shared.web import openapi_examples as ex

#: Where a held call stands, from held through to its outcome.
HeldExecutionStatus = Literal["held", "queued", "running", "completed", "failed", "cancelled"]


class HeldExecutionLinksResponse(BaseModel):
    """HAL-style links for a held execution."""

    self_link: str = Field(
        serialization_alias="self",
        description="This held execution.",
        examples=[f"{ex.BASE_URL}/executions/holds/{ex.HELD_EXECUTION_ID}"],
    )
    result: str | None = Field(
        default=None,
        description="The call's result: the upstream response, or why it never ran. "
        "Present once the call has finished.",
        examples=[f"{ex.BASE_URL}/executions/holds/{ex.HELD_EXECUTION_ID}/result"],
    )
    withdraw: str | None = Field(
        default=None,
        description="Withdraw the held call. Present while it is pending.",
        examples=[f"{ex.BASE_URL}/executions/holds/{ex.HELD_EXECUTION_ID}:withdraw"],
    )
    execution: str | None = Field(
        default=None,
        description="The execution record, once the approved call has run.",
        examples=[f"{ex.BASE_URL}/executions/{ex.EXECUTION_ID}"],
    )


class HeldExecutionResponse(BaseModel):
    """A call held for a human decision, and where it stands."""

    id: str = Field(description="Held execution id (`held_…`).", examples=[ex.HELD_EXECUTION_ID])
    agent_id: str = Field(description="The agent whose call was held.", examples=[ex.AGENT_ID])
    credential_id: str = Field(
        description="The credential selected when the call was held.", examples=[ex.CREDENTIAL_ID]
    )
    api_vendor: str = Field(description="Vendor of the API called.", examples=[ex.API_VENDOR])
    api_name: str = Field(description="Name of the API called.", examples=[ex.API_NAME])
    api_version: str = Field(description="Version of the API called.", examples=[ex.API_VERSION])
    operation_id: str | None = Field(
        default=None, description="Resolved OpenAPI operation id.", examples=[ex.OPERATION_ID]
    )
    method: str = Field(description="HTTP method of the held call.", examples=["POST"])
    path: str = Field(description="Upstream path of the held call.", examples=["/v1/refunds"])
    matched_rule_id: str | None = Field(
        default=None,
        description="The require-approval permission rule that held the call.",
        examples=["apr_6968b398c4e1a2b3d5f60718"],
    )
    state: HeldExecutionState = Field(
        description="The decision: `pending` until a reviewer acts, then `approved`, "
        "`denied`, `expired` or `withdrawn`.",
        examples=["pending"],
    )
    execution_status: HeldExecutionStatus | None = Field(
        default=None,
        description="Where the held call stands: `held` until decided, then `queued`, "
        "`running`, and `completed`, `failed` or `cancelled`.",
        examples=["held"],
    )
    expires_at: datetime = Field(
        description="When a pending held execution expires undecided.",
        examples=["2026-01-15T10:30:00Z"],
    )
    review_url: str = Field(
        description="Review page a signed-in reviewer opens to approve or deny the call. "
        "Carries no credential.",
        examples=[f"{ex.BASE_URL}/app/agents/approvals/{ex.HELD_EXECUTION_ID}"],
    )
    decided_at: datetime | None = Field(
        default=None, description="When it left `pending`.", examples=["2026-01-15T09:42:00Z"]
    )
    decided_by: str | None = Field(
        default=None, description="Reviewer who decided it.", examples=[ex.USER_ID]
    )
    decision_reason: str | None = Field(
        default=None, description="Reviewer's reason.", examples=["Not this account"]
    )
    trace_id: str | None = Field(
        default=None, description="Trace id of the held call.", examples=[ex.TRACE_ID]
    )
    execution_id: str | None = Field(
        default=None,
        description="Execution record written once the approved call ran.",
        examples=[ex.EXECUTION_ID],
    )
    created_at: datetime = Field(
        description="When the call was held.", examples=["2026-01-15T09:30:00Z"]
    )
    updated_at: datetime | None = Field(
        default=None, description="Last change.", examples=["2026-01-15T09:42:00Z"]
    )
    links: HeldExecutionLinksResponse = Field(serialization_alias="_links")


class HeldRequestResponse(BaseModel):
    """The held call exactly as it runs if approved (credentials are injected at run time)."""

    method: str = Field(description="HTTP method.", examples=["POST"])
    url: str = Field(
        description="Upstream URL including the query string.",
        examples=["https://api.stripe.com/v1/refunds?expand[]=charge"],
    )
    body: str | None = Field(
        default=None,
        description="Request body, if any.",
        examples=["charge=ch_3MmlLrLkdIwHu7ix0snN0B15"],
    )
    body_truncated: bool = Field(
        default=False,
        description="True when `body` is cut short for display.",
        examples=[False],
    )


class HeldExecutionDetailResponse(HeldExecutionResponse):
    """A held execution with the agent context and the held request a reviewer decides on."""

    agent_name: str | None = Field(
        default=None, description="Display name of the agent.", examples=["billing-assistant"]
    )
    agent_owner_id: str | None = Field(
        default=None,
        description="The agent's owner; null for an ownerless agent (admin-only).",
        examples=[ex.USER_ID],
    )
    request: HeldRequestResponse | None = Field(default=None, description="The held call.")


class HeldExecutionResultResponse(BaseModel):
    """The outcome of a held call that ran: the execution document the run recorded.

    A held call that never ran answers an `application/problem+json` body
    instead of this one.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "execution_id": ex.EXECUTION_ID,
                    "status": "completed",
                    "http_status": 200,
                    "duration_ms": 412,
                    "body_b64": "eyJpZCI6ICJyZV8xMjMifQ==",
                }
            ]
        }
    )

    execution_id: str = Field(
        description="The execution record written for the released call.",
        examples=[ex.EXECUTION_ID],
    )
    status: ExecutionStatus = Field(
        description="Outcome of the run: `completed`, or `failed` for an upstream, "
        "pipeline or re-authorization failure.",
        examples=["completed"],
    )
    http_status: int | None = Field(
        default=None,
        description="Status the upstream answered with; null when the call never reached it.",
        examples=[200],
    )
    duration_ms: int = Field(
        description="How long the call took, in milliseconds; 0 when it never ran.",
        examples=[412],
    )
    body_b64: str | None = Field(
        default=None,
        description="The upstream response body, base64-encoded; absent when the upstream "
        "returned none. Interpret the decoded bytes against the upstream's own content "
        "type — the body is never inlined as JSON.",
        examples=["eyJpZCI6ICJyZV8xMjMifQ=="],
    )
    problem: dict[str, Any] | None = Field(
        default=None,
        description="Present only when authorization was refused as the released call ran: "
        "the RFC 9457 body the synchronous execute route would have returned.",
    )


class HeldExecutionListResponse(BaseModel):
    """A page of held executions."""

    data: list[HeldExecutionResponse] = Field(description="The page of held executions.")
    has_more: bool = Field(
        description="Whether more results exist after this page.", examples=[True]
    )
    next_cursor: str | None = Field(
        default=None,
        description="Cursor for the next page; null when there are no more results.",
        examples=[ex.CURSOR],
    )


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
