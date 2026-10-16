"""OAuthToken ORM model for storing OAuth2 access and refresh tokens."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AuditableMixin, ControlBase
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime

if TYPE_CHECKING:
    from jentic_one.control.core.schema.credentials import Credential


class OAuthToken(AuditableMixin, ControlBase):
    """Stores OAuth2 access/refresh tokens with expiry and revocation tracking."""

    __tablename__ = "oauth_tokens"
    __table_args__ = (
        # The credential-expiry scanner sweeps every ~2 min in perpetuity,
        # filtering on revoked_at/expires_at and ordering by expires_at. A
        # partial index over live, expiring tokens keeps each sweep an index
        # scan instead of a full table scan as token volume grows.
        Index(
            "ix_oauth_tokens_expiry_scan",
            "expires_at",
            postgresql_where=text("revoked_at IS NULL AND expires_at IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("oat"),
        server_default=func.generate_ksuid("oat"),
    )
    credential_id: Mapped[str] = mapped_column(
        String(30),
        ForeignKey("credentials.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    app_registration_id: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    platform_application_id: Mapped[str | None] = mapped_column(
        String(30), nullable=True, index=True
    )
    issued_to_user: Mapped[str | None] = mapped_column(String(30), nullable=True)
    encrypted_access_token: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # The access token's true vendor-reported expiry; never pre-skewed. The
    # broker's refresher applies the provider's expiry skew at comparison time.
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_refreshed: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Persistent at-most-once dedup markers for the credential-expiry scanner.
    # A token stays in the "expiring" state for the whole warning window and
    # across worker restarts, so the dedup cannot live in memory like the
    # circuit/auth-failure emitters — the scanner stamps these columns once it
    # has emitted the corresponding event and never re-emits while they are set.
    expiring_soon_event_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    expired_event_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    credential: Mapped[Credential] = relationship(back_populates="oauth_token")
