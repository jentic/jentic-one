"""ApiRevision ORM model — versioned snapshot of an API spec."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    column,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AuditableMixin, RegistryBase
from jentic_one.shared.db.ids import new_uuid
from jentic_one.shared.db.types import GUID, UTCDateTime

if TYPE_CHECKING:
    from jentic_one.registry.core.schema.apis import Api
    from jentic_one.registry.core.schema.operations import Operation
    from jentic_one.registry.core.schema.security_schemes import SecurityScheme
    from jentic_one.registry.core.schema.servers import Server
    from jentic_one.registry.core.schema.spec_files import SpecFile


class ApiRevision(AuditableMixin, RegistryBase):
    """A versioned snapshot of an API specification."""

    __tablename__ = "api_revisions"
    __table_args__ = (
        UniqueConstraint("api_id", "spec_digest", name="uq_api_revisions_api_id_spec_digest"),
        Index("ix_api_revisions_api_id", "api_id"),
        Index(
            "ix_api_revisions_one_active",
            "api_id",
            unique=True,
            postgresql_where=text("state IN ('published', 'imported')"),
            sqlite_where=text("state IN ('published', 'imported')"),
        ),
        Index(
            "ix_api_revisions_api_id_created_at_id",
            "api_id",
            column("created_at").desc(),
            column("id").desc(),
        ),
        Index(
            "ix_api_revisions_source_url_state",
            "source_url",
            "state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=new_uuid,
        server_default=func.gen_random_uuid(),
    )
    api_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("apis.id", ondelete="CASCADE"),
        nullable=False,
    )
    state: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'draft'"))
    origin: Mapped[str | None] = mapped_column(String(50), nullable=True)
    #: sha256 of the spec content, or NULL when the source carried no sha. Must be
    #: NULL — never '' — for sha-less specs: '' is a *value* under
    #: uq_api_revisions_api_id_spec_digest and would collapse distinct sha-less
    #: specs for the same api_id into one revision, whereas NULLs are distinct on
    #: both Postgres and SQLite. Derivation lives in CreateRevisionStage (#780).
    spec_digest: Mapped[str | None] = mapped_column(String(100), nullable=True)
    #: For an overlay-origin revision: the ``spec_digest`` of the *base* revision this
    #: overlay was materialized on top of (the revision it superseded). Lets the Flow-3
    #: sweep compare upstream changes against the overlay's **base** rather than the
    #: overlaid digest, so it can tell "upstream unchanged vs base" (no-op) from
    #: "upstream changed vs base" (a genuine conflict). NULL for non-overlay revisions
    #: and for pre-existing overlay revisions (A3 treats NULL as "unknown base → today's
    #: served-digest compare"; lazy self-heal on the next re-materialize).
    overlay_base_digest: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_content_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    submitted_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    operation_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    promoted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    api: Mapped[Api] = relationship(
        back_populates="revisions",
        foreign_keys=[api_id],
    )
    spec_files: Mapped[list[SpecFile]] = relationship(
        back_populates="revision", cascade="all, delete-orphan"
    )
    operations: Mapped[list[Operation]] = relationship(
        back_populates="revision", cascade="all, delete-orphan"
    )
    servers: Mapped[list[Server]] = relationship(
        back_populates="revision", cascade="all, delete-orphan"
    )
    security_schemes: Mapped[list[SecurityScheme]] = relationship(
        back_populates="revision", cascade="all, delete-orphan"
    )
