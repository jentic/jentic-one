"""ActorScopeGrant ORM model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime
from jentic_one.shared.db.utils import utcnow


class ActorScopeGrant(AuditableMixin, AdminBase):
    """Maps a scope grant to an actor (user or agent)."""

    __tablename__ = "actor_scope_grants"
    __table_args__ = (
        UniqueConstraint("actor_id", "scope", name="uq_actor_scope_grants_actor_scope"),
        Index("ix_actor_scope_grants_scope", "scope"),
        Index("ix_actor_scope_grants_actor", "actor_id", "actor_type"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("asg"),
        server_default=func.generate_ksuid("asg"),
    )
    actor_id: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, server_default=func.now()
    )
    granted_by: Mapped[str | None] = mapped_column(String(30), nullable=True)
