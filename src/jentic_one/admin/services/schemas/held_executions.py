"""Execution approval service-layer models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from jentic_one.shared.auth.grant_renewal import GrantRenewalPolicy
from jentic_one.shared.models.held_executions import ApprovalDecision


class HeldExecutionView(BaseModel):
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
    #: ``execution`` (a require-approval rule) or ``grant_renewal`` (an ended binding).
    kind: str = "execution"
    expires_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None
    decision_reason: str | None = None
    trace_id: str | None = None
    execution_id: str | None = None
    created_at: datetime
    updated_at: datetime | None = None
    #: Where the held call stands: ``held`` until decided, then ``queued``,
    #: ``running`` and a terminal ``completed``, ``failed`` or ``cancelled``.
    execution_status: str | None = None
    #: The filing agent's explanation of why it made the call, when it gave one.
    agent_reason: str | None = None


class HeldRequestView(BaseModel):
    """The held call as the reviewer sees it — what runs if they approve."""

    method: str
    url: str
    body: str | None = None
    body_truncated: bool = False


class RenewalContextView(BaseModel):
    """What a reviewer of a ``grant_renewal`` hold decides on: the binding as it stands."""

    #: Which limit ended it (``end_date``, ``idle``, ``uses``); None if renewed since.
    expired_reason: str | None = None
    #: Its last recorded use (the idle clock's stamp); None if never used.
    last_used_at: datetime | None = None
    #: Its current policy.
    expires_at: datetime | None = None
    max_idle_period: int | None = None
    idle_warning: bool = False
    after_expiry: str = "hold"
    max_quota: int | None = None
    quota_remaining: int | None = None
    #: Whether the reader may re-grant access (else only deny).
    can_renew: bool = False
    #: Whether approving can renew the binding itself (``renewal`` on the
    #: decision). False where this process cannot read the control database (a
    #: split deployment): the reviewer renews the binding through its own renew
    #: endpoint, then approves without ``renewal``.
    renew_on_decide: bool = True


class HeldExecutionDetailView(HeldExecutionView):
    """An approval plus the agent context and the held request."""

    agent_name: str | None = None
    agent_owner_id: str | None = None
    request: HeldRequestView | None = None
    #: For a ``grant_renewal`` hold: the binding it would renew.
    renewal: RenewalContextView | None = None


class DecideInput(BaseModel):
    """A reviewer's decision.

    ``renewal`` is the fresh policy for a ``grant_renewal`` hold's binding
    (required to approve one, refused on any other hold); ``run`` says whether
    the held call then runs (``False``: the binding is renewed and the call
    fails, for a stale call).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    decision: ApprovalDecision
    reason: str | None = None
    renewal: GrantRenewalPolicy | None = None
    run: bool = True
