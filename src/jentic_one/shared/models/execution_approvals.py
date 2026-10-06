"""Execution-approval enums shared by the broker hold path and the admin surface."""

from __future__ import annotations

from enum import StrEnum


class ExecutionApprovalState(StrEnum):
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
