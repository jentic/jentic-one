"""ExecutionApproval ORM model — holds a pending human-approval request.

One row per held execution job. Created atomically with the held job; updated
by the reviewer decision (approve, deny) or the expiry sweep. Sits in the
admin DB beside ``jobs`` so the `:decide` endpoint can flip both the approval
state and the job status in one transaction with no cross-DB writes.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime


class ExecutionApproval(AuditableMixin, AdminBase):
    """Tracks the state of a held execution approval request.

    ``state`` lifecycle: ``pending`` → ``approved`` | ``denied`` | ``expired``
    | ``withdrawn`` (terminal). Terminal rows are not deleted on settlement;
    they serve as the audit trail for reviewer decisions.

    Cross-DB references (``credential_id``, ``matched_rule_id``) carry no FK
    constraints — the same precedent as ``execution_records.credential_id``.
    ``execution_id`` is populated when the approved job completes, linking the
    approval row to the ``execution_records`` row that ran the operation.
    """

    __tablename__ = "execution_approvals"
    __table_args__ = (
        Index("ix_execution_approvals_agent_state", "agent_id", "state"),
        Index("ix_execution_approvals_state_expires", "state", "expires_at"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("exap"),
        server_default=func.generate_ksuid("exap"),
    )

    # The held execution job this approval is paired with.
    job_id: Mapped[str] = mapped_column(String(30), nullable=False, index=True)

    # Invoking agent (admin DB — no FK needed; same schema).
    agent_id: Mapped[str] = mapped_column(String(30), nullable=False)

    # Credential selected at hold time (cross-DB ref, no FK).
    credential_id: Mapped[str] = mapped_column(String(30), nullable=False)

    # Reviewer context — what the human sees on the review page.
    api_vendor: Mapped[str] = mapped_column(String(128), nullable=False)
    api_name: Mapped[str] = mapped_column(String(128), nullable=False)
    api_version: Mapped[str] = mapped_column(String(128), nullable=False)
    operation_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)

    # SHA-256 of (agent_id, credential_id, method, path, canonical body) — used
    # to join an identical retry to the existing pending row.
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    # Rule that triggered the hold (cross-DB ref, no FK). Nullable because the
    # matched rule may have been deleted before the reviewer inspects the row.
    matched_rule_id: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Approval state machine.
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")

    # Hold expiry — set at creation from ``execution_approvals.ttl_seconds``.
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    # Reviewer decision fields — populated on decide.
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(30), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Original trace id — for correlating the approval row to the inbound
    # request span in distributed traces.
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Populated once the approved job runs and an execution_records row exists.
    execution_id: Mapped[str | None] = mapped_column(String(30), nullable=True)
