"""CatalogUpdateCheck ORM model — per-API upstream-change bookkeeping (Flow 3).

One row per **local** registered API (``local_api_id``, unique — never the
upstream manifest id, per D-005a) recording what the update-notify sweep last
observed for that API's upstream spec URL:

- ``last_seen_etag`` — the validator sent back as ``If-None-Match`` on the next
  sweep so an unchanged spec answers ``304`` and transfers no body.
- ``last_seen_digest`` — ``sha256`` of the last fetched spec bytes. Recorded for
  observability / future weak-ETag handling; the current MVP dedupes on
  ``last_notified_digest`` and compares against the registered revision's
  ``spec_digest``, so this column is written but not yet read.
- ``last_notified_digest`` — the digest that last produced a
  ``catalog.update_available`` event, so a persistently-changed spec notifies
  **once** rather than every sweep.
- ``last_checked_at`` — when the sweep last probed this API.

The table is deliberately narrow and relationship-free: it is sweep scratch
state, not part of the API aggregate, so it does not widen ``apis``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import RegistryBase
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import GUID, UTCDateTime
from jentic_one.shared.db.utils import utcnow


class CatalogUpdateCheck(RegistryBase):
    """Per-API record of the last upstream-spec observation for update notify."""

    __tablename__ = "catalog_update_checks"
    __table_args__ = (Index("ix_catalog_update_checks_local_api_id", "local_api_id", unique=True),)

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("cuc"),
        server_default=func.generate_ksuid("cuc"),
    )
    #: The local registry API this row tracks (``apis.id``). No FK constraint:
    #: the row is cheap scratch state and a dropped API is reconciled by the
    #: sweep (a check with no matching registered spec url is simply ignored).
    local_api_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    #: The upstream spec URL probed (the API's revision ``source_url``).
    spec_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    last_seen_etag: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seen_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_notified_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The event *class* that ``last_notified_digest`` was emitted under
    #: (``catalog.update_available`` vs ``catalog.update_conflicts_overlay``). The sweep
    #: dedupes on the pair ``(last_notified_digest, last_notified_event_class)`` so a
    #: digest re-classified between the two classes (e.g. an overlaid API whose upstream
    #: now collides with the overlay's base) emits the new class **once** instead of
    #: being wrongly deduped against the old one. NULL until the first notify.
    last_notified_event_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, server_default=func.now()
    )
