"""Execution-approval enums shared by the broker hold path and the admin surface."""

from __future__ import annotations

from enum import StrEnum


class HeldExecutionState(StrEnum):
    """Lifecycle of an execution approval; every state but ``pending`` is terminal."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    WITHDRAWN = "withdrawn"


class ApprovalDecision(StrEnum):
    """A reviewer's decision on a pending approval."""

    APPROVE = "approve"
    DENY = "deny"


class HeldExecutionKind(StrEnum):
    """Why a call is held.

    ``execution``: a ``require-approval`` rule matched; approving runs it.
    ``grant_renewal``: the agent's binding to the credential has ended (its end
    date, idle limit or uses); approving re-grants the binding first,
    and may then run the call.
    """

    EXECUTION = "execution"
    GRANT_RENEWAL = "grant_renewal"
