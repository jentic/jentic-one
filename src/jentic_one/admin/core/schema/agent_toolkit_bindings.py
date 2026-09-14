"""AgentToolkitBinding ORM model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid


class AgentToolkitBinding(AuditableMixin, AdminBase):
    """Binding between a broker-executing actor and a toolkit.

    ``agent_id`` holds an agent (``agnt_…``) or — since theme-5 Phase 4 — a
    service account (``sva_…``) migrated from a ``jntc_live_`` toolkit key,
    whose broker access keeps deriving through its toolkit until the Phase-6a
    flattening. The two actor kinds live in sibling tables, so the column
    carries no FK; ``AgentService.delete`` removes an agent's bindings
    explicitly, and service accounts archive rather than hard-delete.
    """

    __tablename__ = "agent_toolkit_bindings"
    __table_args__ = (
        UniqueConstraint("agent_id", "toolkit_id", name="uq_agent_toolkit_bindings_agent_toolkit"),
        Index("ix_agent_toolkit_bindings_agent_id", "agent_id"),
        Index("ix_agent_toolkit_bindings_toolkit_id", "toolkit_id"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("atb"),
        server_default=func.generate_ksuid("atb"),
    )
    agent_id: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    toolkit_id: Mapped[str] = mapped_column(String(255), nullable=False)
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
