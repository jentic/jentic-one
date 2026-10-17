"""AssertionJti ORM model — single-use records for RFC 7523 assertion ``jti`` values.

The auth surface writes one row per accepted JWT-bearer assertion, keyed on
``(issuer, jti_digest)``, in the same transaction that verifies the
assertion. The unique index makes the write the replay check: a second
exchange of the same ``jti`` from the same issuer collides on it, on every
process that shares the admin database.

The ``jti`` itself is never stored, only its SHA-256 digest: the claim is
caller-chosen and unbounded in length, and the digest keeps every row the same
size. ``expires_at`` is the assertion's ``exp`` plus a clock-skew grace; once
it has passed the assertion can no longer verify anywhere, so the row is only
pruned (or overwritten by a fresh assertion reusing the ``jti``) after then.
Append-only, so it does not use :class:`AuditableMixin`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime
from jentic_one.shared.db.utils import utcnow


class AssertionJti(AdminBase):
    """One accepted assertion ``jti`` for one issuer, kept until the assertion expires."""

    __tablename__ = "assertion_jtis"
    __table_args__ = (
        Index("ix_assertion_jtis_issuer_jti_digest", "issuer", "jti_digest", unique=True),
        # Serves the bounded prune of expired rows.
        Index("ix_assertion_jtis_expires_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("ajti"),
        server_default=func.generate_ksuid("ajti"),
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, server_default=func.now()
    )
    # The verified ``iss`` claim: the agent id the assertion was signed for.
    issuer: Mapped[str] = mapped_column(String(255), nullable=False)
    # Hex SHA-256 of the ``jti`` claim.
    jti_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
