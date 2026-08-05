"""LatestRelease ORM model — the last-known-latest app release version.

Holds the newest ``jentic-one`` release the CLI has discovered (via
``jenticctl update``) and reported to this backend. The UI reads it (alongside
the running ``__version__``) to decide whether to show an "update available"
banner. The backend never fetches this itself — it only stores what the CLI
reports — so there is no outbound egress.

Although the table only ever holds a single logical row, it follows the
project's ORM convention (ksuid ``id`` primary key with a unique natural key)
rather than a fixed-string primary key, so it needs no ksuid-exemption. The
singleton is enforced at the repository layer by upserting on the fixed
``key`` (:data:`LATEST_RELEASE_KEY`).
"""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid

#: The one natural key ever written — the table holds at most this one row.
LATEST_RELEASE_KEY = "latest_release"


class LatestRelease(AuditableMixin, AdminBase):
    """Single-row holder of the last-known-latest app release version."""

    __tablename__ = "latest_releases"

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("lrel"),
        server_default=func.generate_ksuid("lrel"),
    )
    key: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
