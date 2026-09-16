"""ServiceAccount ORM model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime


class ServiceAccount(AuditableMixin, AdminBase):
    """Platform service account — a non-interactive actor for system integrations.

    Theme-8 Phase 1 (service-account → agent migration): ``migrated_to_actor_id``
    is the migration job's **sole idempotency short-circuit** (the
    ``toolkit_keys.migrated_actor_id`` twin, ``control/core/schema/
    toolkit_keys.py``). Non-NULL means the row is done: either the successor
    agent's ``agnt_…`` id (full migration) or the literal sentinel string
    ``skipped`` (skip-but-stamp for pending/rejected/archived rows — OQ-1).
    The stamp — never the account name — keys idempotency, and the W6 writer
    guards refuse every mutation verb on a stamped row.

    ``migrated_at`` is the stamp *timestamp*: the NF-3 verify criterion ("no
    ``api_key_hash`` change after the stamp timestamp") and the N3
    minimum-stamp-age sweep gate are both relative to it.
    """

    __tablename__ = "service_accounts"
    __table_args__ = (
        Index("ix_service_accounts_owner_id", "owner_id"),
        Index("ix_service_accounts_status", "status"),
        Index("ix_service_accounts_migrated_to_actor_id", "migrated_to_actor_id"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("sva"),
        server_default=func.generate_ksuid("sva"),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    owner_id: Mapped[str] = mapped_column(
        String(30),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    registered_by: Mapped[str] = mapped_column(String(30), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(
        String(30),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    denial_reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    denied_by: Mapped[str | None] = mapped_column(String(30), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Theme-8 Phase 1 stamp pair — see class docstring.
    migrated_to_actor_id: Mapped[str | None] = mapped_column(String(30), nullable=True)
    migrated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
