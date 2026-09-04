"""Refresh token ORM model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime, json_variant


class RefreshToken(AuditableMixin, AdminBase):
    """Opaque refresh token with family lineage tracking for rotation."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_token_hash", "token_hash", unique=True),
        Index("ix_refresh_tokens_actor_id", "actor_id"),
        Index("ix_refresh_tokens_token_family_id", "token_family_id"),
        Index("ix_refresh_tokens_expires_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("rt"),
        server_default=func.generate_ksuid("rt"),
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(json_variant(), nullable=False)
    token_family_id: Mapped[str] = mapped_column(String(30), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    replaced_by_id: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Scoped to third-party delegation only: set when tokens are issued via the
    # authorization code flow through a registered OAuth client. NULL for platform
    # client logins (the SPA), agent JWKS assertions, and service account auth.
    oauth_client_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Grant-channel lineage (D4): mirrors access_tokens.oauth_grant_id.
    # Refresh rotation re-checks the grant row on every turn (revoked → fail
    # closed); grant :revoke sweeps rows by this column.
    oauth_grant_id: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
