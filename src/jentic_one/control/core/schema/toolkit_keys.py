"""ToolkitKey ORM model."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AuditableMixin, ControlBase
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime, json_variant

if TYPE_CHECKING:
    from jentic_one.control.core.schema.toolkits import Toolkit


class ToolkitKey(AuditableMixin, ControlBase):
    """API key issued for a toolkit — stores argon2 hash, never the plaintext."""

    __tablename__ = "toolkit_keys"
    __table_args__ = (Index("ix_toolkit_keys_toolkit_id", "toolkit_id"),)

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("ck"),
        server_default=func.generate_ksuid("ck"),
    )
    toolkit_id: Mapped[str] = mapped_column(
        String(30), ForeignKey("toolkits.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    allowed_ips: Mapped[list[str] | None] = mapped_column(json_variant(), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    key_preview: Mapped[str] = mapped_column(String(50), nullable=False)
    hashed_key: Mapped[str] = mapped_column(String(255), nullable=False)
    lookup_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Theme-5 Phase 4 (key retirement): the service account (``sva_…``,
    # cross-DB FK-less reference into the admin database) this key retired
    # to. Stamped by the migration job; NULL means not (yet) migrated.
    # Revoking a migrated key must also disable this actor — the plaintext
    # authenticates as the service account for the deprecation window.
    migrated_actor_id: Mapped[str | None] = mapped_column(String(30), nullable=True)

    toolkit: Mapped[Toolkit] = relationship(back_populates="keys")
