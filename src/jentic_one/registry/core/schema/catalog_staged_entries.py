"""CatalogStagedEntry ORM model — one listing change staged by a sync run.

A sync run writes each change its source's listing yields here, in batches and
outside any long transaction. The run's single apply transaction then folds the
staged rows into ``catalog_entries`` and deletes them. Text is stored raw (as the
connector gave it); sanitising happens at apply.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import RegistryBase
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime, json_variant
from jentic_one.shared.db.utils import utcnow

STAGED_OP_UPSERT = "upsert"
STAGED_OP_REMOVE = "remove"


class CatalogStagedEntry(RegistryBase):
    """A listing change staged by a sync run, awaiting its apply."""

    __tablename__ = "catalog_staged_entries"
    __table_args__ = (
        UniqueConstraint(
            "sync_run_id", "external_id", name="uq_catalog_staged_entries_run_external_id"
        ),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("cstg"),
        server_default=func.generate_ksuid("cstg"),
    )
    sync_run_id: Mapped[str] = mapped_column(String(30), nullable=False)
    op: Mapped[str] = mapped_column(String(16), nullable=False)
    external_id: Mapped[str] = mapped_column(String(287), nullable=False)
    catalog_api_id_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[str | None] = mapped_column(Text, nullable=True)
    spec_ref: Mapped[dict[str, Any] | None] = mapped_column(json_variant(), nullable=True)
    human_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(json_variant(), nullable=False, default=list)
    content_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, server_default=func.now()
    )
