"""Auth-code flow details for an OAuth app registration (class-table extension)."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jentic_one.shared.db.base import AuditableMixin, ControlBase
from jentic_one.shared.db.types import json_variant

if TYPE_CHECKING:
    from jentic_one.control.core.schema.oauth_app_registrations import OAuthAppRegistration


class AuthorizationCodeAppRegistrationDetails(AuditableMixin, ControlBase):
    """Auth-code specific fields for an OAuth app registration."""

    __tablename__ = "authorization_code_app_registration_details"

    id: Mapped[str] = mapped_column(
        String(30),
        ForeignKey("oauth_app_registrations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    encrypted_client_secret: Mapped[str] = mapped_column(Text, nullable=False)
    authorize_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    token_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    # Pre-populated scope list surfaced to the connect wizard.
    default_scopes: Mapped[list[str] | None] = mapped_column(json_variant(), nullable=True)
    secret_last_rotated_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    registration: Mapped[OAuthAppRegistration] = relationship(
        back_populates="authorization_code_details"
    )
