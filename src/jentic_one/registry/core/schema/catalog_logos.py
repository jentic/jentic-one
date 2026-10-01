"""CatalogLogo ORM model — server-side cache of catalog vendor logos.

One row per upstream logo URL (``source_url``, unique) taken from a manifest
entry's ``image``. The registry fetches the image once (SSRF-guarded, size
capped), keeps the bytes here and serves them from ``GET /catalog/{api_id}/logo``,
so browsers never contact the upstream host and repeat views cost no egress.

``status`` records the outcome of the last fetch:

- ``ok`` — ``content``/``content_type`` hold a validated raster image.
- ``unsupported`` — the upstream answered, but not with a PNG/JPEG/WebP/GIF
  image; retried after the normal max-age.
- ``error`` — the fetch failed (network, non-2xx, over the size cap); retried sooner. A previously
  cached image is kept and served while the row is in this state.

The table is cache state, not part of any aggregate: no relationships, and a
row whose URL drops out of the manifest is simply never read again.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import RegistryBase
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime
from jentic_one.shared.db.utils import utcnow


class CatalogLogo(RegistryBase):
    """Cached bytes (or fetch outcome) for one upstream catalog logo URL."""

    __tablename__ = "catalog_logos"
    __table_args__ = (Index("ix_catalog_logos_source_url", "source_url", unique=True),)

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("clg"),
        server_default=func.generate_ksuid("clg"),
    )
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    #: ``sha256`` of ``content``; served as the response's strong ``ETag``.
    digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The upstream validator, sent back as ``If-None-Match`` on revalidation.
    upstream_etag: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, server_default=func.now()
    )
