"""Webhook delivery ORM model.

One row per (event x destination): the durable record of an outbound send and
its retries. Fan-out inserts one row per subscribed notification endpoint, so
each destination is retried independently and one broken receiver cannot block
the others.

The row *is* the queue. ``status`` plus ``next_attempt_at`` is what a claiming
sweeper selects on (``pending``/``failed`` rows whose time has come), which is
why they carry a composite index. ``attempt_count`` drives the capped
exponential backoff, and a row that exhausts its attempts becomes ``dead``
rather than being deleted — a dead-letter you can inspect and resend.

Note the deliberate absence of the response *body*: only the status code and a
truncated error are kept. A destination's response can contain anything, and
this table is not the place to accumulate it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import UTCDateTime


class WebhookDelivery(AuditableMixin, AdminBase):
    """A single outbound delivery attempt-set for one event to one endpoint."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        Index("ix_webhook_deliveries_claim", "status", "next_attempt_at"),
        Index("ix_webhook_deliveries_event_id", "event_id"),
        Index("ix_webhook_deliveries_endpoint_created", "endpoint_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("whdl"),
        server_default=func.generate_ksuid("whdl"),
    )
    event_id: Mapped[str] = mapped_column(
        String(30), ForeignKey("webhook_events.id", ondelete="CASCADE"), nullable=False
    )
    endpoint_id: Mapped[str] = mapped_column(
        String(30), ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
