"""Junction table connecting agents to credentials, carrying permission rules.

Aligns with theme-5's target model (direct agent<->credential bindings with
rules at the binding, replacing today's toolkit-mediated indirection). In
phase 1 the broker still resolves via toolkits, so this table is populated
alongside the toolkit graph but not yet read by the broker. When theme-5
lands, this becomes the authoritative source.

`agent_id` is FK-less: agents live in the admin DB (cross-DB FKs forbidden).
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AuditableMixin, ControlBase
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import json_variant


class AgentCredentialPermission(AuditableMixin, ControlBase):
    """One row per agent<->credential binding, holding the ordered rule list.

    Rules are stored as a JSON list of {method, path, effect} dicts; the first
    matching rule wins (allowlist semantics, default-deny). Future refactor may
    normalise these into a child table if per-rule audit/history is needed.
    """

    __tablename__ = "agent_credential_permissions"
    __table_args__ = (
        UniqueConstraint("agent_id", "credential_id", name="uq_agent_credential"),
        Index("ix_acp_agent", "agent_id"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("acp"),
        server_default=func.generate_ksuid("acp"),
    )
    agent_id: Mapped[str] = mapped_column(String(30), nullable=False)
    credential_id: Mapped[str] = mapped_column(
        String(30),
        ForeignKey("credentials.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Ordered list of {method, path, effect} rule dicts. Deliberately JSON in
    # phase 1 — normalise later if rules need per-item queryability.
    rules: Mapped[list[dict[str, str]]] = mapped_column(
        json_variant(), nullable=False, default=list
    )
