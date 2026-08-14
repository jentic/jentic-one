"""Agent ORM model."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime


class Agent(AuditableMixin, AdminBase):
    """Platform agent entity — an autonomous actor registered by a user."""

    __tablename__ = "agents"
    __table_args__ = (
        Index("ix_agents_owner_id", "owner_id"),
        Index("ix_agents_status", "status"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("agnt"),
        server_default=func.generate_ksuid("agnt"),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    owner_id: Mapped[str | None] = mapped_column(
        String(30),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    registered_by: Mapped[str] = mapped_column(String(30), nullable=False)
    parent_agent_id: Mapped[str | None] = mapped_column(
        String(30),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    approved_by: Mapped[str | None] = mapped_column(
        String(30),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    denial_reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    denied_by: Mapped[str | None] = mapped_column(String(30), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    jwks: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)
    registration_access_token_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    rat_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Claim token (self-registered agent ownership). Minted at /register when a
    # claim-token minter is installed (see auth/core/claim.py); the plaintext is
    # returned once and only the sha256 hash is stored. The registering human
    # presents the plaintext to POST /agents/{id}:claim to take ownership. Kept
    # SEPARATE from the RAT columns above on purpose: the RAT backs the RFC 7592
    # management flow and is nulled by set_approval, so reusing it would drop the
    # claim token exactly when the agent is approved.
    claim_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
