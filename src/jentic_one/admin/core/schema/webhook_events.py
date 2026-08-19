"""Webhook event ORM model.

One row per internal platform event selected for outbound delivery.

``source_event_id`` is deliberately ``NOT NULL`` and unique per endpoint: it is
the deduplication guarantee. Senders retry, so the same event legitimately
arrives more than once, and "we already have this" must be a database fact
rather than an application hope. When a sender supplies no usable id, the
ingress synthesises one from a hash of the raw body so the constraint always
has something to bite on. A unique violation on insert therefore means
"already seen" — the caller answers ``200`` rather than an error, because the
sender did nothing wrong.

Writing the row is the whole of the request-path work: the payload is stored
and the request returns. Fan-out and delivery happen later, off the request.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.db.types import json_variant


class WebhookEvent(AuditableMixin, AdminBase):
    """An accepted webhook event, awaiting or having completed fan-out."""

    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint(
            "endpoint_id", "source_event_id", name="uq_webhook_events_endpoint_source"
        ),
        Index("ix_webhook_events_endpoint_id", "endpoint_id"),
        Index("ix_webhook_events_type_created", "event_type", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("whev"),
        server_default=func.generate_ksuid("whev"),
    )
    endpoint_id: Mapped[str] = mapped_column(
        String(30), ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), nullable=False
    )
    source_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(  # type: ignore[type-arg]
        json_variant(), nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
