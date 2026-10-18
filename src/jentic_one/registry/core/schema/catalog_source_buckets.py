"""CatalogSourceBucket ORM model — the shared rate bucket of one catalog source.

A row exists only for a source configured with ``requests_per_second``. Every
process that fetches from the source (web replicas, job workers, an external
indexer) takes a token from this row before each request, so the rate holds
across all of them. ``blocked_until`` is set when the source answers ``429``
and holds both lanes until it passes.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Float, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import RegistryBase
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime
from jentic_one.shared.db.utils import utcnow


class CatalogSourceBucket(RegistryBase):
    """Token-bucket state for one rate-limited catalog source."""

    __tablename__ = "catalog_source_buckets"
    __table_args__ = (
        UniqueConstraint("source_slug", name="uq_catalog_source_buckets_source_slug"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("cbkt"),
        server_default=func.generate_ksuid("cbkt"),
    )
    source_slug: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Tokens left as of ``refilled_at``; refilled lazily on every take.
    tokens: Mapped[float] = mapped_column(Float, nullable=False)
    refilled_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    #: Both lanes wait until this passes (set from a ``429``'s ``Retry-After``).
    blocked_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True, onupdate=utcnow
    )
