"""OAuthClient ORM model — registered third-party OAuth applications."""

from __future__ import annotations

from sqlalchemy import Boolean, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import string_array_variant


class OAuthClient(AuditableMixin, AdminBase):
    """A registered confidential OAuth client that can initiate authorization flows.

    Third-party applications register here to receive authorization codes at
    their own redirect URIs. Clients authenticate at the token endpoint with
    a client_secret (argon2id hash stored) in addition to PKCE (S256).
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
    client_secret_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    redirect_uris: Mapped[list[str]] = mapped_column(string_array_variant(), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    require_consent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    allowed_scopes: Mapped[list[str] | None] = mapped_column(
        string_array_variant(), nullable=True, default=None
    )
