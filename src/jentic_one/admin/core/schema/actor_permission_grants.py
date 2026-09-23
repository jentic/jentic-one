"""ActorPermissionGrant ORM model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime
from jentic_one.shared.db.utils import utcnow


class ActorPermissionGrant(AuditableMixin, AdminBase):
    """Maps a permission grant to any actor type (user, agent, service_account)."""

    __tablename__ = "actor_permission_grants"
    __table_args__ = (
        UniqueConstraint(
            "actor_id", "permission", name="uq_actor_permission_grants_actor_permission"
        ),
        Index("ix_actor_permission_grants_permission", "permission"),
        Index("ix_actor_permission_grants_actor", "actor_id", "actor_type"),
    )

    #: The ``asg`` KSUID prefix is a stored value carried by every existing row, so
    #: it stays as-is: re-prefixing would split the table's ids into two eras for no
    #: gain (ids are opaque) and would turn a pure rename into a data migration.
    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("asg"),
        server_default=func.generate_ksuid("asg"),
    )
    actor_id: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    permission: Mapped[str] = mapped_column(String(64), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, server_default=func.now()
    )
    granted_by: Mapped[str | None] = mapped_column(String(30), nullable=True)
