"""CatalogEntry ORM model — one row per entry a catalog source lists.

``entry_id`` (``{source_slug}/{external_id}``) is the source-scoped identity;
``catalog_api_id`` is the resolver's cross-source API identity, stored at sync
apply. ``catalog_api_id_hint`` keeps the connector's raw hint so the resolver can
re-run over stored rows without a new listing. Text columns hold sanitised values
only: the sync apply caps and cleans them before they land here.

A row whose source stopped listing it is tombstoned (``removed_at`` set), never
deleted, so a later listing that lists it again revives the same row.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import RegistryBase
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime, json_variant
from jentic_one.shared.db.utils import utcnow


class CatalogEntryRow(RegistryBase):
    """One entry listed by a catalog source (live or tombstoned)."""

    __tablename__ = "catalog_entries"
    __table_args__ = (
        UniqueConstraint("entry_id", name="uq_catalog_entries_entry_id"),
        UniqueConstraint(
            "source_slug", "external_id", name="uq_catalog_entries_source_slug_external_id"
        ),
        Index("ix_catalog_entries_catalog_api_id", "catalog_api_id"),
        Index("ix_catalog_entries_source_slug_removed_at", "source_slug", "removed_at"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("cent"),
        server_default=func.generate_ksuid("cent"),
    )
    entry_id: Mapped[str] = mapped_column(String(320), nullable=False)
    source_slug: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(287), nullable=False)
    catalog_api_id_hint: Mapped[str | None] = mapped_column(String(512), nullable=True)
    catalog_api_id: Mapped[str] = mapped_column(String(320), nullable=False)
    title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    spec_ref: Mapped[dict[str, Any] | None] = mapped_column(json_variant(), nullable=True)
    human_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    tags: Mapped[list[str]] = mapped_column(json_variant(), nullable=False, default=list)
    content_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    removed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True, onupdate=utcnow
    )
