"""Job ORM model for tracking asynchronous job execution."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import json_variant


class Job(AuditableMixin, AdminBase):
    """Tracks asynchronous job execution with parent/child hierarchy support."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("job"),
        server_default=func.generate_ksuid("job"),
    )
    parent_job_id: Mapped[str | None] = mapped_column(
        String(30),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # Type of the actor that enqueued the job; the id is carried on created_by.
    actor_type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'user'")
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_id: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Visibility deadline for a claimed (RUNNING) job: the claim query treats a
    # RUNNING job whose visible_at has passed as claimable again, so a job
    # orphaned by a dead worker/pod is recovered rather than stuck forever.
    # NULL while QUEUED; set on claim, cleared on terminal status.
    visible_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # How many times this job has been claimed for processing. Incremented on
    # each claim; once it exceeds the retry budget a failing job is dead-lettered
    # instead of requeued.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    payload: Mapped[dict | None] = mapped_column(  # type: ignore[type-arg]
        json_variant(), nullable=True, server_default=text("NULL")
    )

    parent: Mapped[Job | None] = relationship(back_populates="children", remote_side=[id])
    children: Mapped[list[Job]] = relationship(back_populates="parent")
