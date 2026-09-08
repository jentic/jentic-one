"""Api ORM model — aggregate root for the registry domain."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AuditableMixin, RegistryBase
from jentic_one.shared.db.ids import new_uuid
from jentic_one.shared.db.types import GUID

if TYPE_CHECKING:
    from jentic_one.registry.core.schema.api_revisions import ApiRevision
    from jentic_one.registry.core.schema.overlays import Overlay


class Api(AuditableMixin, RegistryBase):
    """A registered API identified by vendor + name + version."""

    __tablename__ = "apis"
    __table_args__ = (
        UniqueConstraint("vendor", "name", "version", name="uq_apis_vendor_name_version"),
        ForeignKeyConstraint(
            ["current_revision_id"],
            ["api_revisions.id"],
            name="fk_apis_current_revision_id",
            use_alter=True,
        ),
        Index("ix_apis_vendor", "vendor"),
        Index("ix_apis_vendor_name", "vendor", "name"),
        Index("ix_apis_created_at_id", "created_at", "id", postgresql_using="btree"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=new_uuid,
        server_default=func.gen_random_uuid(),
    )
    vendor: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    # The catalog identity slug this API was imported from (e.g.
    # `nytimes.com/article_search`) — the only identity field where the vendor
    # and sub-API are separable, which display surfaces need for friendly
    # titles. NULL for manually-imported/inline APIs that never had one.
    # (`api_id` would shadow the ubiquitous `Api.id` UUID naming, hence the
    # `catalog_` prefix.)
    catalog_api_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    icon_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    current_revision_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    revision_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    operation_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    revisions: Mapped[list[ApiRevision]] = relationship(
        back_populates="api",
        foreign_keys="[ApiRevision.api_id]",
    )
    current_revision: Mapped[ApiRevision | None] = relationship(
        foreign_keys=[current_revision_id],
        uselist=False,
        lazy="joined",
        post_update=True,
    )
    overlays: Mapped[list[Overlay]] = relationship(
        back_populates="api",
        cascade="all, delete-orphan",
        foreign_keys="[Overlay.api_id]",
    )
