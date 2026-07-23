"""AgentToolkitBinding ORM model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid


class AgentToolkitBinding(AuditableMixin, AdminBase):
    """Binding between an agent and a toolkit."""

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
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    toolkit_id: Mapped[str] = mapped_column(String(255), nullable=False)
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
