"""Device-flow (RFC 8628) credential registration + transient polling state.

Parallel to `oauth_client_credentials` but for the OAuth 2.0 Device
Authorization Grant. Same hybrid pattern as documented in the agent-driven integration flow
implementation plan: static registration fields (client_id, token_url,
authorization_endpoint) plus transient polling state that is cleared once
the flow reaches `connected`.

Keyed 1:1 with `credentials.id` (CASCADE).
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jentic_one.shared.db.base import AuditableMixin, ControlBase
from jentic_one.shared.db.types import json_variant

if TYPE_CHECKING:
    from jentic_one.control.core.schema.credentials import Credential


class DeviceFlowCredential(AuditableMixin, ControlBase):
    """OAuth 2.0 device-flow registration + pending-flow state for a credential."""

    __tablename__ = "device_flow_credentials"

    # Shares the credentials.id PK — 1:1 relationship.
    id: Mapped[str] = mapped_column(
        String(30),
        ForeignKey("credentials.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # ---- static registration (populated on credential create) ---------------
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    token_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    authorization_endpoint: Mapped[str] = mapped_column(String(2048), nullable=False)

    # ---- transient polling state (nullable; cleared on `connected`) ---------
    # RFC 8628 device_code — long random string returned by the vendor's device
    # authorization endpoint, encrypted at rest.
    encrypted_device_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    # RFC 8628 user_code — short human-typable code (e.g. "ABCD-1234") shown on
    # the review page and typed by the user at `verification_uri`.
    user_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    verification_uri: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # Optional RFC 8628 prefilled verification URI (embeds user_code).
    verification_uri_complete: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # Vendor-specified minimum poll interval (seconds).
    poll_interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_polled_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    device_code_expires_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ---- scopes (per-credential) --------------------------------------------
    # The scopes the initiator asked for at `:connect` time.
    requested_scopes: Mapped[list[str] | None] = mapped_column(json_variant(), nullable=True)
    # The scopes the human confirmed at `:confirm` time (what we asked the
    # vendor for). May be a subset of `requested_scopes` plus any defaults the
    # human left checked.
    granted_scopes: Mapped[list[str] | None] = mapped_column(json_variant(), nullable=True)

    credential: Mapped[Credential] = relationship(back_populates="device_flow_credential")
