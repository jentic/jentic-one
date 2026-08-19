"""OAuthClient ORM model — registered third-party OAuth applications."""

from __future__ import annotations

from sqlalchemy import ARRAY, Boolean, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid


class OAuthClient(AuditableMixin, AdminBase):
    """A registered OAuth client that can initiate authorization flows.

    Third-party applications register here to receive authorization codes at
    their own redirect URIs. No client_secret is stored — PKCE (S256) provides
    sufficient security for public clients.
    """

    __tablename__ = "oauth_clients"
    __table_args__ = (
        Index("ix_oauth_clients_client_id", "client_id", unique=True),
        Index("ix_oauth_clients_active", "active"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("oac"),
        server_default=func.generate_ksuid("oac"),
    )
    client_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    redirect_uris: Mapped[list[str]] = mapped_column(ARRAY(String(2048)), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    require_consent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
