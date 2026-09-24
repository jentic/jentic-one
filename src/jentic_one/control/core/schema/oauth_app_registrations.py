"""OAuth application registration — shared or user-owned OAuth-app config.

Base table for class-table inheritance: every registration lives here and
gets exactly one row in a flow-kind-specific extension table
(``authorization_code_app_registration_details`` or
``device_authorization_app_registration_details``). Credentials FK to this
base and dereference the extension by ``flow_kind``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AuditableMixin, ControlBase
from jentic_one.shared.db.ids import generate_ksuid

if TYPE_CHECKING:
    from jentic_one.control.core.schema.authorization_code_app_registration_details import (
        AuthorizationCodeAppRegistrationDetails,
    )
    from jentic_one.control.core.schema.credentials import Credential
    from jentic_one.control.core.schema.device_authorization_app_registration_details import (
        DeviceAuthorizationAppRegistrationDetails,
    )


class OAuthAppRegistration(AuditableMixin, ControlBase):
    """A registered OAuth application at a vendor (client_id + endpoints)."""

    __tablename__ = "oauth_app_registrations"
    __table_args__ = (
        Index("ix_oauth_app_registrations_api_vendor", "api_vendor"),
        Index("ix_oauth_app_registrations_flow_kind", "flow_kind"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("oar"),
        server_default=func.generate_ksuid("oar"),
    )
    # Admin-facing label, e.g. "MyOrg GitHub app". Distinct from api_vendor
    # so the same vendor can host multiple registrations (prod vs. sandbox).
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    api_vendor: Mapped[str] = mapped_column(String(100), nullable=False)
    # Flow discriminator: authorization_code | device_authorization.
    # Determines which extension table holds the flow-specific fields.
    flow_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    # Plaintext per RFC 6749 §2.2 — client_id is a public identifier.
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # The catalog API slug this OAuth app targets (e.g.
    # ``googleapis-com/gmail``). Stamped verbatim onto the credential's
    # ``catalog_api_id`` at connect time so the operations preview on the
    # rules page resolves against a real registered API. Nullable to keep
    # pre-refactor rows loadable; the create endpoint requires it for all
    # new rows.
    catalog_api_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Vendor family label ("Gmail") — distinct from ``name``, which is the
    # admin's per-registration label ("MyOrg Prod Gmail"). Nullable for
    # back-compat; UI falls back to ``api_vendor`` when absent.
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Kill switch: false blocks new connects and refuses refresh on
    # dependent credentials (existing tokens continue injecting until expiry).
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    authorization_code_details: Mapped[AuthorizationCodeAppRegistrationDetails | None] = (
        relationship(
            back_populates="registration",
            cascade="all, delete-orphan",
            uselist=False,
            lazy="selectin",
        )
    )
    device_authorization_details: Mapped[DeviceAuthorizationAppRegistrationDetails | None] = (
        relationship(
            back_populates="registration",
            cascade="all, delete-orphan",
            uselist=False,
            lazy="selectin",
        )
    )
    credentials: Mapped[list[Credential]] = relationship(
        back_populates="oauth_app_registration",
    )
