"""Toolkit ORM model."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AuditableMixin, ControlBase
from jentic_one.shared.db.ids import generate_ksuid

if TYPE_CHECKING:
    from jentic_one.control.core.schema.toolkit_credential_bindings import (
        ToolkitCredentialBinding,
    )
    from jentic_one.control.core.schema.toolkit_keys import ToolkitKey


class Toolkit(AuditableMixin, ControlBase):
    """A scoped bundle of credentials and permissions issued to an agent or service."""

    __tablename__ = "toolkits"

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("tk"),
        server_default=func.generate_ksuid("tk"),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    keys: Mapped[list[ToolkitKey]] = relationship(
        back_populates="toolkit",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    bindings: Mapped[list[ToolkitCredentialBinding]] = relationship(
        back_populates="toolkit",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
