"""External identity link ORM model."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid


class ExternalIdentity(AuditableMixin, AdminBase):
    """Maps an external IDP subject to an internal user."""

    __tablename__ = "external_identities"
    __table_args__ = (
        UniqueConstraint("issuer", "external_subject", name="uq_ext_id_issuer_subject"),
        Index("ix_ext_id_user_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("eid"),
        server_default=func.generate_ksuid("eid"),
    )
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    issuer: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    external_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[str] = mapped_column(
        String(30), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
