"""ToolkitPermissionRule ORM model — per-binding permission rules."""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AuditableMixin, ControlBase
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import json_variant


class ToolkitPermissionRule(AuditableMixin, ControlBase):
    """Row-level permission rule scoped to a toolkit-credential binding."""

    __tablename__ = "toolkit_permission_rules"
    __table_args__ = (
        Index(
            "ix_toolkit_permission_rules_binding_seq",
            "toolkit_id",
            "credential_id",
            "sequence",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("tpr"),
        server_default=func.generate_ksuid("tpr"),
    )
    toolkit_id: Mapped[str] = mapped_column(
        String(30), ForeignKey("toolkits.id", ondelete="CASCADE"), nullable=False
    )
    credential_id: Mapped[str] = mapped_column(
        String(30), ForeignKey("credentials.id", ondelete="CASCADE"), nullable=False
    )
    effect: Mapped[str] = mapped_column(String(10), nullable=False)
    methods: Mapped[list[str] | None] = mapped_column(json_variant(), nullable=True)
    path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # ``MATCH`` is a reserved word in SQLite and a Python soft keyword; use
    # ``match_mode`` throughout (schema field, column, and dict key).
    match_mode: Mapped[str] = mapped_column(
        String(10), nullable=False, default="regex", server_default=text("'regex'")
    )
    operations: Mapped[list[str] | None] = mapped_column(json_variant(), nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    comment: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
