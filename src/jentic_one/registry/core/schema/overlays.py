"""Overlay ORM model — JSONB overlays attached to an Api aggregate."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AuditableMixin, RegistryBase
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import GUID, UTCDateTime, json_variant

if TYPE_CHECKING:
    from jentic_one.registry.core.schema.apis import Api


class Overlay(AuditableMixin, RegistryBase):
    """A JSONB overlay document attached to an Api (not revision-specific)."""

    __tablename__ = "overlays"
    __table_args__ = (
        Index("ix_overlays_api_id", "api_id"),
        Index("ix_overlays_api_id_created_at_id", "api_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("ovr"),
        server_default=func.generate_ksuid("ovr"),
    )
    api_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("apis.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_revision_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    #: The revision produced by materializing this overlay (set by the ingest job on
    #: a successful confirm). Distinct from ``target_revision_id`` (the base revision
    #: the overlay was authored against). No FK: the revision lives in the same DB but
    #: the link is advisory — a pruned revision simply leaves this dangling, and the
    #: overlay is re-materialized on the next confirm/re-import.
    confirmed_revision_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'")
    )
    document: Mapped[dict] = mapped_column(json_variant(), nullable=False)  # type: ignore[type-arg]
    contributed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confirmed_by_execution_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    deprecated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    api: Mapped[Api] = relationship(back_populates="overlays")
