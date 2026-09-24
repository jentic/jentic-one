"""Sigv4Credential ORM model for storing AWS SigV4 signing credentials."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AuditableMixin, ControlBase
from jentic_one.shared.db.ids import generate_ksuid

if TYPE_CHECKING:
    from jentic_one.control.core.schema.credentials import Credential


class Sigv4Credential(AuditableMixin, ControlBase):
    """Stores AWS SigV4 signing material for a credential.

    ``access_key_id`` is a public identifier (not a secret by AWS's definition),
    so it is stored plaintext like the basic-auth username; the secret access key
    and any temporary session token are encrypted at rest. ``region``/``service``
    are the signing scope (e.g. ``us-east-1`` / ``aoss``).
    """

    __tablename__ = "sigv4_credentials"

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("sigv4"),
        server_default=func.generate_ksuid("sigv4"),
    )
    credential_id: Mapped[str] = mapped_column(
        String(30),
        ForeignKey("credentials.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    access_key_id: Mapped[str] = mapped_column(String(128), nullable=False)
    encrypted_secret_access_key: Mapped[str] = mapped_column(Text, nullable=False)
    secret_preview: Mapped[str | None] = mapped_column(String(16), nullable=True)
    encrypted_session_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    service: Mapped[str] = mapped_column(String(64), nullable=False)

    credential: Mapped[Credential] = relationship(
        back_populates="sigv4_credential", lazy="selectin"
    )
