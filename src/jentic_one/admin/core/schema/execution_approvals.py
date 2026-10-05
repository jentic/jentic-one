"""ExecutionApproval ORM model — the human-approval record for a held execution job.

One row per held execution job, created in the same transaction as the job.
Updated by the reviewer decision (approve / deny), the expiry sweep, or the
agent withdrawing the job. Lives in the admin DB beside ``jobs`` so ``:decide``
updates the approval and releases or fails the job in one transaction.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime


class ExecutionApproval(AuditableMixin, AdminBase):
    """The approval state of one held execution job.

    ``state`` lifecycle: ``pending`` → ``approved`` | ``denied`` | ``expired``
    | ``withdrawn`` (terminal). Terminal rows are kept as the decision record.

    ``credential_id`` and ``matched_rule_id`` reference control-DB rows by id
    only (no FK), the same precedent as ``execution_records.credential_id``.
    ``execution_id`` is set once the approved job runs and its
    ``execution_records`` row exists. The run outcome itself is the job's
    status and ``job_results`` row, not duplicated here.
    """

    __tablename__ = "execution_approvals"
    __table_args__ = (
        Index("ix_execution_approvals_agent_state", "agent_id", "state"),
        Index("ix_execution_approvals_state_expires", "state", "expires_at"),
        # At most one pending row per fingerprint: an identical retry joins the
        # existing hold instead of filing a second one. Settled rows drop out
        # of the index so the same request can be filed again later.
        Index(
            "uq_execution_approvals_pending_fingerprint",
            "request_fingerprint",
            unique=True,
            postgresql_where=text("state = 'pending'"),
            sqlite_where=text("state = 'pending'"),
        ),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("exap"),
        server_default=func.generate_ksuid("exap"),
    )
    job_id: Mapped[str] = mapped_column(
        String(30),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id: Mapped[str] = mapped_column(String(30), nullable=False)
    credential_id: Mapped[str] = mapped_column(String(30), nullable=False)
    api_vendor: Mapped[str] = mapped_column(String(128), nullable=False)
    api_name: Mapped[str] = mapped_column(String(128), nullable=False)
    api_version: Mapped[str] = mapped_column(String(128), nullable=False)
    operation_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    # SHA-256 of (agent_id, credential_id, method, path, canonical body).
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    matched_rule_id: Mapped[str | None] = mapped_column(String(30), nullable=True)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default=text("'pending'")
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(30), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    execution_id: Mapped[str | None] = mapped_column(String(30), nullable=True)
