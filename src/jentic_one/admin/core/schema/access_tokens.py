"""Access token ORM model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime, json_variant


class AccessToken(AuditableMixin, AdminBase):
    """Opaque access token stored as a SHA-256 hash."""

    __tablename__ = "access_tokens"
    __table_args__ = (
        Index("ix_access_tokens_token_hash", "token_hash", unique=True),
        Index("ix_access_tokens_actor_id", "actor_id"),
        Index("ix_access_tokens_token_family_id", "token_family_id"),
        Index("ix_access_tokens_expires_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("at"),
        server_default=func.generate_ksuid("at"),
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(json_variant(), nullable=False)
    token_family_id: Mapped[str] = mapped_column(String(30), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # True for short-lived ephemeral mint tokens (issue_access_only), which carry
    # a deliberately downscoped snapshot and must NOT be re-broadened to the
    # actor's live grants at resolution time. False for long-lived access+refresh
    # pairs, whose scopes are resolved live from actor_scope_grants.
    is_ephemeral: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    # Scoped to third-party delegation only: set when tokens are issued via the
    # authorization code flow through a registered OAuth client. NULL for platform
    # client logins (the SPA), agent JWKS assertions, and service account auth.
    # Used to invalidate tokens when an admin deactivates the issuing client.
    oauth_client_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
