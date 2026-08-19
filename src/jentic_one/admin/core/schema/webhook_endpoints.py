"""Webhook endpoint ORM model.

An endpoint is one configured **notification** destination — an outbound URL we
POST signed events to. ``target_url`` is the operator-supplied URL and is
therefore **always** sent through the SSRF-guarding egress transport, never a
bare client.

**Secret storage.** HMAC signing needs the *key*, not a digest of it, so a
webhook secret cannot be irreversibly hashed the way a password is.
``secret_encrypted`` therefore holds the AES-256-GCM envelope produced by
``EncryptionService`` (format ``<key_id>:<b64>``), which keeps the plaintext out of
the database while remaining recoverable. ``secret_hash`` is retained alongside it
purely as a **non-reversible fingerprint**, used to detect reuse and to compare
secrets without decrypting.

``previous_secret_encrypted`` exists so a secret can be rotated without dropping
events: for a grace period both keys are offered on outbound deliveries, and
only then is the old one dropped. Without it, rotation means guaranteed lost
deliveries for anything already in flight.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime, json_variant


class WebhookEndpoint(AuditableMixin, AdminBase):
    """A configured outbound notification endpoint."""

    __tablename__ = "webhook_endpoints"
    __table_args__ = (Index("ix_webhook_endpoints_name", "name", unique=True),)

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("whep"),
        server_default=func.generate_ksuid("whep"),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    previous_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    previous_secret_expires_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    target_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    event_types: Mapped[list] = mapped_column(  # type: ignore[type-arg]
        json_variant(), nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
