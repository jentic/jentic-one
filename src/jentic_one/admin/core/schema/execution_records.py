"""ExecutionRecord ORM model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Index, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime, json_variant


class ExecutionRecord(AuditableMixin, AdminBase):
    """Append-only record of a capability execution."""

    __tablename__ = "execution_records"
    __table_args__ = (
        Index("ix_execution_records_started_at", "started_at"),
        Index("ix_execution_records_trace_id", "trace_id"),
        Index("ix_execution_records_toolkit_started", "toolkit_id", "started_at"),
        Index("ix_execution_records_status", "status"),
        Index("ix_execution_records_actor", "actor_id", "actor_type"),
        Index("ix_execution_records_credential_id", "credential_id"),
        Index(
            "ix_execution_records_repeated_failure_scan",
            "actor_id",
            "toolkit_id",
            "operation_id",
            "started_at",
            postgresql_where=text("status = 'failed'"),
        ),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("exec"),
        server_default=func.generate_ksuid("exec"),
    )
    toolkit_id: Mapped[str] = mapped_column(String(30), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    operation_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    api_vendor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    api_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    api_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    api_host: Mapped[str | None] = mapped_column(String(256), nullable=True)
    pinned_revisions: Mapped[dict | None] = mapped_column(json_variant(), nullable=True)  # type: ignore[type-arg]
    http_status: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    error: Mapped[str | None] = mapped_column(String(128), nullable=True)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    origin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Credential attribution (#740). Nullable for historical rows, executions
    # that used inline auth, and executions that failed before the resolver
    # picked a credential. No FK: ``credentials`` lives in the control DB
    # (cross-DB, same reason ``toolkit_id`` has no FK here). Indexed for the
    # audit-console "what did this credential do?" query.
    credential_id: Mapped[str | None] = mapped_column(String(30), nullable=True)
    credential_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
