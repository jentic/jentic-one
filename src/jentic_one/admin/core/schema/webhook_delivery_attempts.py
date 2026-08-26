"""Webhook delivery-attempt ORM model.

One row per **individual** outbound attempt against a :class:`WebhookDelivery`.
The parent ``webhook_deliveries`` row keeps only the *last* outcome
(``last_status_code`` / ``last_error`` / ``last_attempt_at`` / ``duration_ms``),
which is all the queue needs to schedule the next retry. This child table is the
durable **history**: it lets the UI show every attempt (status code, duration,
error category, when) instead of only the most recent one, which is what powers
the per-endpoint response-time view and the attempt timeline in the drawer.

Kept deliberately narrow and append-only — one insert per real attempt, never
updated — and it carries no response *body* for the same reason the parent
doesn't: a destination's response can contain anything and this is not the place
to accumulate it. Rows are removed by cascade when the parent delivery is pruned
or its endpoint deleted.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from jentic_one.shared.db.base import AdminBase, AuditableMixin
from jentic_one.shared.db.ids import generate_ksuid


class WebhookDeliveryAttempt(AuditableMixin, AdminBase):
    """A single recorded outbound attempt for one delivery."""

    __tablename__ = "webhook_delivery_attempts"
    __table_args__ = (
        # The history read is always "attempts for this delivery, newest first",
        # so index the parent + the ordering key together.
        Index(
            "ix_webhook_delivery_attempts_delivery_created",
            "delivery_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        default=lambda: generate_ksuid("whda"),
        server_default=func.generate_ksuid("whda"),
    )
    delivery_id: Mapped[str] = mapped_column(
        String(30),
        ForeignKey("webhook_deliveries.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 1-based ordinal of this attempt within the delivery (mirrors the parent's
    # ``attempt_count`` at the moment the attempt completed).
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Categorised, non-sensitive failure reason (never the raw exception text —
    # see ``delivery._categorize_error``); null on success.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Wall-clock duration of this attempt in milliseconds; null if the attempt
    # never produced a timed result.
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
