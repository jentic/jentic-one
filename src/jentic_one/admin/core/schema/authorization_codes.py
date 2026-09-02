"""Authorization code ORM model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime


class AuthorizationCode(AuditableMixin, AdminBase):
    """Single-use authorization code for the AuthCode+PKCE flow."""

    __tablename__ = "authorization_codes"
    __table_args__ = (Index("ix_authz_code_hash", "code_hash", unique=True),)

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("acd"),
        server_default=func.generate_ksuid("acd"),
    )
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(30), nullable=False)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    code_challenge: Mapped[str] = mapped_column(String(128), nullable=False)
    scopes: Mapped[str] = mapped_column(String(1024), nullable=False, server_default="openid")
    nonce: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Set at consent for consent_model='agent' clients (§4.4 step 4): the
    # freshly-minted oauth_client_grants row the exchange must honour. NULL
    # keeps today's act-as-user exchange path byte-identical.
    grant_id: Mapped[str | None] = mapped_column(String(30), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
