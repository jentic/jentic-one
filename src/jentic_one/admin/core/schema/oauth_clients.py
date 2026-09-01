"""OAuthClient ORM model — registered third-party OAuth applications."""

from __future__ import annotations

from sqlalchemy import Boolean, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import string_array_variant
from jentic_one.shared.models.oauth_clients import (
    OAuthClientApprovalStatus,
    OAuthConsentModel,
    OAuthRegistrationSource,
    TokenEndpointAuthMethod,
)


class OAuthClient(AuditableMixin, AdminBase):
    """A registered third-party OAuth client that can initiate authorization flows.

    Third-party applications register here to receive authorization codes at
    their own redirect URIs. Confidential clients (``token_endpoint_auth_method
    = client_secret_basic``) authenticate at the token endpoint with a
    client_secret (argon2id hash stored) in addition to PKCE (S256); public
    clients (``token_endpoint_auth_method = none``) store no secret and rely
    on PKCE alone (D5).

    ``approval_status`` is the admin admission gate (D7): only ``approved``
    rows may pass /authorize validation or mint tokens. ``active`` stays the
    independent kill switch for approved clients.
    """

    __tablename__ = "oauth_clients"
    __table_args__ = (
        Index("ix_oauth_clients_client_id", "client_id", unique=True),
        Index("ix_oauth_clients_active", "active"),
        Index("ix_oauth_clients_approval_status", "approval_status"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("oac"),
        server_default=func.generate_ksuid("oac"),
    )
    client_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # NULL for public (secret-less) clients; the service layer enforces that a
    # NULL hash and token_endpoint_auth_method='none' always travel together.
    client_secret_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
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
    token_endpoint_auth_method: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=TokenEndpointAuthMethod.CLIENT_SECRET_BASIC.value,
        server_default=TokenEndpointAuthMethod.CLIENT_SECRET_BASIC.value,
    )
    consent_model: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default=OAuthConsentModel.USER.value,
        server_default=OAuthConsentModel.USER.value,
    )
    registration_source: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default=OAuthRegistrationSource.ADMIN.value,
        server_default=OAuthRegistrationSource.ADMIN.value,
    )
    software_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # server_default 'approved' is deliberate back-compat: every pre-3a row was
    # admin-created and live, so upgraded rows keep working (§4.1).
    approval_status: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default=OAuthClientApprovalStatus.APPROVED.value,
        server_default=OAuthClientApprovalStatus.APPROVED.value,
    )
