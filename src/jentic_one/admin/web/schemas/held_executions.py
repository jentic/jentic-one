"""Held execution request/response schemas — calls parked for a human decision."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from jentic_one.shared.agent_text import MAX_AGENT_TEXT_INPUT_LEN
from jentic_one.shared.auth.expiry import as_utc
from jentic_one.shared.auth.grant_policy import (
    AFTER_EXPIRY_DESCRIPTION,
    MAX_QUOTA,
    AfterExpiry,
    GrantEndReason,
)
from jentic_one.shared.iso_duration import IdlePeriodRequest, IdlePeriodResponse
from jentic_one.shared.models.executions import ExecutionStatus
from jentic_one.shared.models.held_executions import (
    ApprovalDecision,
    HeldExecutionKind,
    HeldExecutionState,
)
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
    explain: str | None = Field(
        default=None,
        description=(
            "Explain why the agent made the call, for the reviewer. Present while it is "
            "pending and not yet explained; only the agent that filed it may use it."
        ),
        examples=[f"{ex.BASE_URL}/executions/holds/{ex.HELD_EXECUTION_ID}:explain"],
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
    kind: HeldExecutionKind = Field(
        default=HeldExecutionKind.EXECUTION,
        description="Why the call is held: `execution` (a require-approval rule matched) or "
        "`grant_renewal` (the agent's binding to the credential has ended; approving re-grants "
        "its access first).",
        examples=["execution"],
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
    agent_reason: str | None = Field(
        default=None,
        description=(
            "Why the agent says it made the call, in its own words, sent with `:explain` "
            "after the call was held. Unverified; null when the agent gave no reason."
        ),
        examples=["Customer 4821 asked for a replacement order"],
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


class HeldRenewalContextResponse(BaseModel):
    """The binding a `grant_renewal` hold would renew, as it stands now."""

    expired_reason: GrantEndReason | None = Field(
        default=None,
        description="Which limit ended it: `end_date`, `idle` or `uses`; null if it holds "
        "again (renewed since).",
        examples=["idle"],
    )
    last_used_at: datetime | None = Field(
        default=None,
        description="Its last recorded use; null if never used.",
        examples=["2026-01-01T09:30:00Z"],
    )
    expires_at: datetime | None = Field(
        default=None, description="Its current end date.", examples=["2026-01-10T00:00:00Z"]
    )
    max_idle_period: IdlePeriodResponse | None = Field(
        default=None,
        description="Its current idle limit, as an ISO 8601 duration.",
        examples=["P14D"],
    )
    idle_warning: bool = Field(
        default=False,
        description="Whether its owner is warned before it ends (end date or idle limit).",
        examples=[False],
    )
    after_expiry: AfterExpiry = Field(
        default=AfterExpiry.HOLD,
        description="What a call through it gets once it has ended: `hold` or `refuse`.",
        examples=["hold"],
    )
    max_quota: int | None = Field(
        default=None, description="Its use count (1 is single use).", examples=[1]
    )
    quota_remaining: int | None = Field(
        default=None, description="Uses left under its count.", examples=[0]
    )
    can_renew: bool = Field(
        default=False,
        description="Whether the caller may re-grant access (else it can only deny): a "
        "person who owns the agent and the credential, or an `org:admin`.",
        examples=[True],
    )
    renew_on_decide: bool = Field(
        default=True,
        description="Whether approving with `renewal` renews the binding here. False where "
        "this deployment's admin surface cannot renew (it runs apart from the control "
        "database), and `can_renew` is then unknown: renew the binding through "
        "`PATCH /agents/{agent_id}/credentials/{credential_id}`, then approve without "
        "`renewal`.",
        examples=[True],
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
    renewal: HeldRenewalContextResponse | None = Field(
        default=None, description="For a `grant_renewal` hold: the binding it would renew."
    )


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


class GrantRenewalRequest(BaseModel):
    """Re-grant access for an ended binding.

    Every field left out sets no such limit, except the end date: left out, the
    binding runs again for as long as it was granted for, capped at the org's
    maximum (``null`` sets no end date).
    """

    model_config = ConfigDict(extra="forbid")

    expires_at: datetime | None = Field(
        default=None,
        description=(
            "New end date (RFC 3339, in the future); null for no end date. Left out, the "
            "binding runs again for as long as it was granted for, capped at the org's maximum."
        ),
        examples=["2026-02-15T09:30:00Z"],
    )
    max_idle_period: IdlePeriodRequest | None = Field(
        default=None,
        description="New idle limit, as an ISO 8601 duration (P1D to P999D, e.g. P14D);"
        " null for none.",
        examples=["P14D"],
    )
    idle_warning: bool = Field(
        default=False,
        description="Warn the agent's owner before the binding ends (its end date or idle limit).",
        examples=[False],
    )
    after_expiry: AfterExpiry | None = Field(
        default=None,
        description=AFTER_EXPIRY_DESCRIPTION + " Left out, the binding keeps its own.",
        examples=["hold"],
    )
    max_quota: int | None = Field(
        default=None,
        ge=1,
        le=MAX_QUOTA,
        description="New use count (1 is single use); null for unlimited calls.",
        examples=[1],
    )

    @field_validator("expires_at")
    @classmethod
    def _future(cls, value: datetime | None) -> datetime | None:
        # A naive value is read as UTC, as the direct binding endpoint reads it.
        if value is not None and as_utc(value) <= datetime.now(UTC):
            raise ValueError("expires_at must be in the future")
        return value


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
    renewal: GrantRenewalRequest | None = Field(
        default=None,
        description="Approving a `grant_renewal` hold: the binding's fresh policy, which replaces "
        "its old one (renewing also restarts its idle clock and restores its uses). Required "
        "to approve one; refused on any other hold.",
    )
    run: bool = Field(
        default=True,
        description="With `renewal`: whether the held call runs once access is renewed. "
        "`false` renews and fails the call (for a stale one).",
        examples=[True],
    )


class ExplainHeldExecutionRequest(BaseModel):
    """The filing agent's explanation of why it made a held call."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"reason": "Customer 4821 asked for a replacement order"}]},
    )

    reason: str = Field(
        min_length=1,
        max_length=MAX_AGENT_TEXT_INPUT_LEN,
        description=(
            "One or two sentences on why the call is needed, shown to the reviewer as the "
            "agent's own claim. Control and text-direction characters are removed and the "
            "text is cut to 1024 characters."
        ),
    )
