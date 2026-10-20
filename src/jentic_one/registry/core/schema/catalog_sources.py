"""CatalogSource ORM model — per-source sync state for the multi-source catalog.

One row per configured catalog source (``catalog.sources[]`` in YAML). The
configuration itself lives in the config file; this table holds only sync state
plus a digest of the configuration, so credentials and other settings never
reach the database.

Sync coordination lives here too: ``sync_lease_owner`` / ``sync_lease_until`` are
the cross-process single-flight lease for one source's sync, ``sync_wanted_at``
is set by a stale read and drained by the background scanner, and
``reresolve_wanted_at`` asks the scanner to re-run the ``catalog_api_id``
resolver over the stored rows (no listing, no network).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import RegistryBase
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime
from jentic_one.shared.db.utils import utcnow

#: ``last_status`` values.
SOURCE_STATUS_NEVER_SYNCED = "never_synced"
SOURCE_STATUS_OK = "ok"
SOURCE_STATUS_FAILED = "failed"
SOURCE_STATUS_SUSPECT_EMPTY = "suspect_empty"


class CatalogSourceRow(RegistryBase):
    """Sync state for one configured catalog source."""

    __tablename__ = "catalog_sources"
    __table_args__ = (UniqueConstraint("slug", name="uq_catalog_sources_slug"),)

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("csrc"),
        server_default=func.generate_ksuid("csrc"),
    )
    slug: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    #: SHA-256 of the source's configuration (settings, visibility, trust, ...).
    config_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    visibility: Mapped[str] = mapped_column(String(16), nullable=False)
    trust: Mapped[str] = mapped_column(String(16), nullable=False)
    precedence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    #: Opaque delta token from the connector, passed back as ``since``.
    watermark: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    listing_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    live_entries: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_applied_run_id: Mapped[str | None] = mapped_column(String(30), nullable=True)
    last_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=SOURCE_STATUS_NEVER_SYNCED,
        server_default=SOURCE_STATUS_NEVER_SYNCED,
    )
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    sync_lease_owner: Mapped[str | None] = mapped_column(String(30), nullable=True)
    sync_lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    sync_wanted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    #: A requested sync (operator or push) that the scanner starts once this time
    #: has passed and no run holds the lease.
    sync_due_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    #: Set when the next run should skip the mass-removal guard; the run that
    #: claims the lease consumes it.
    sync_forced_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    reresolve_wanted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    #: Digest of the resolver's config inputs (mappings, trust, enabled) that this
    #: source's stored ``catalog_api_id`` values were last resolved under.
    resolver_inputs_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True, onupdate=utcnow
    )
