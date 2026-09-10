"""AgentPermissionRule ORM model — per agent-credential-binding permission rules."""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AuditableMixin, ControlBase
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import json_variant


class AgentPermissionRule(AuditableMixin, ControlBase):
    """Row-level permission rule scoped to an agent-credential binding.

    Keyed ``(agent_id, credential_id)``. The agent lives in the admin
    database, so ``agent_id`` is a plain string column with no FK; rule
    cleanup on agent deletion is an application-level sweep (no CASCADE
    across databases). ``credential_id`` FKs ``credentials`` so rules
    cascade with credential deletion.

    ``UNIQUE (agent_id, credential_id, sequence)`` is both a legitimate
    invariant for an ordered first-match-wins list (duplicate sequence
    numbers would make evaluation order backend-dependent) and the
    idempotency key the theme-5 flattening job's ``ON CONFLICT DO
    NOTHING`` conflicts on (PR #35 review, finding P-03). Its backing
    unique index also serves binding-scoped rule lookups.
    """

    __tablename__ = "agent_permission_rules"
    __table_args__ = (
        UniqueConstraint(
            "agent_id",
            "credential_id",
            "sequence",
            name="uq_agent_permission_rules_binding_seq",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("apr"),
        server_default=func.generate_ksuid("apr"),
    )
    agent_id: Mapped[str] = mapped_column(String(30), nullable=False)
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
