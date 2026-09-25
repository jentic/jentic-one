"""Device-flow (RFC 8628) details for an OAuth app registration (class-table extension)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jentic_one.shared.db.base import AuditableMixin, ControlBase
from jentic_one.shared.db.types import json_variant

if TYPE_CHECKING:
    from jentic_one.control.core.schema.oauth_app_registrations import OAuthAppRegistration


class DeviceAuthorizationAppRegistrationDetails(AuditableMixin, ControlBase):
    """Device-flow specific fields for an OAuth app registration.

    Device flow (RFC 8628) is a public client — no ``client_secret`` column.
    """

    __tablename__ = "device_authorization_app_registration_details"

    id: Mapped[str] = mapped_column(
        String(30),
        ForeignKey("oauth_app_registrations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # RFC 8628 device authorization endpoint (POST target for user_code).
    authorization_endpoint: Mapped[str] = mapped_column(String(2048), nullable=False)
    # RFC 8628 token endpoint (poll target while waiting for user consent).
    token_endpoint: Mapped[str] = mapped_column(String(2048), nullable=False)
    default_scopes: Mapped[list[str] | None] = mapped_column(json_variant(), nullable=True)

    registration: Mapped[OAuthAppRegistration] = relationship(
        back_populates="device_authorization_details"
    )
