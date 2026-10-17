"""CatalogSyncRun ORM model — one attempt to sync one catalog source."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import RegistryBase
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime
from jentic_one.shared.db.utils import utcnow

#: ``status`` values.
RUN_STATUS_RUNNING = "running"
RUN_STATUS_APPLIED = "applied"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_SUSPECT_EMPTY = "suspect_empty"
RUN_STATUS_SUPERSEDED = "superseded"


class CatalogSyncRun(RegistryBase):
    """One sync attempt for one source, with its outcome counters."""

    __tablename__ = "catalog_sync_runs"
    __table_args__ = (
        Index("ix_catalog_sync_runs_source_slug_started_at", "source_slug", "started_at"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("csyn"),
        server_default=func.generate_ksuid("csyn"),
    )
    source_slug: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    is_full: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    forced: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    staged: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    upserted: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    removed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RUN_STATUS_RUNNING, server_default=RUN_STATUS_RUNNING
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, server_default=func.now()
    )
