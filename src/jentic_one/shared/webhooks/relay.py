"""Relay: copies newly emitted internal events into the outbound queue.

A scanner-style loop (same shape as ``CredentialExpiryScanner`` and the delivery
dispatcher) that walks the ``events`` table forwards and hands each new row to
fan-out. This is what makes the notification half *real*: operators do not
publish events by hand — a credential quietly expires, the platform emits
``credential.expired``, and the relay turns that into signed outbound deliveries.

**The cursor.** Progress is a ``(created_at, id)`` tuple rather than a bare
timestamp, so two events created in the same microsecond cannot hide behind each
other — ``EventRepository.list_after_cursor`` compares the pair. The cursor is
**derived, not stored**: on first use it is read back from the highest internal
event already relayed (see
``WebhookEventRepository.max_relayed_source_event_id``), so a restart resumes
where the relay stopped instead of skipping whatever was emitted while the
process was down. With nothing relayed yet it starts at *now*, so installing this
does not dump the entire event history at whatever endpoint happens to exist.

**Why advancing the cursor can be cheap.** Fan-out is idempotent — deliveries are
keyed by ``(endpoint_id, source_event_id)`` — so re-reading an event is harmless.
That lets the loop favour never *losing* an event over never repeating one, which
is the right trade when the duplicate is caught by a unique constraint anyway.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.repos.event_repo import EventRepository
from jentic_one.admin.repos.webhook_repo import WebhookEventRepository
from jentic_one.admin.services.webhooks.fanout import (
    WebhookFanoutService,
    build_notification_payload,
)
from jentic_one.shared.config import WebhookConfig
from jentic_one.shared.webhooks import metrics as webhook_metrics

if TYPE_CHECKING:
    from jentic_one.shared.context import Context

logger = structlog.get_logger(__name__)


class InternalEventRelay:
    """Periodically fans newly emitted internal events out to subscribers."""

    def __init__(
        self,
        ctx: Context,
        *,
        config: WebhookConfig | None = None,
        poll_interval: float | None = None,
        batch_limit: int | None = None,
        relay_lag: float | None = None,
    ) -> None:
        self._ctx = ctx
        self._fanout = WebhookFanoutService(ctx)
        # Config supplies the knobs (poll cadence, batch, relay lag); explicit
        # per-arg overrides win, so a test can pin ``relay_lag=0.0`` without
        # building a whole config.
        self._config = config or WebhookConfig()
        self._poll_interval = (
            poll_interval if poll_interval is not None else self._config.relay_poll_interval_s
        )
        self._batch_limit = (
            batch_limit if batch_limit is not None else self._config.relay_batch_limit
        )
        self._relay_lag = relay_lag if relay_lag is not None else self._config.relay_lag_s
        self._cursor: tuple[datetime, str] | None = None
        self._running = False

    async def run(self) -> None:
        """Main loop — relay periodically until cancelled.

        A failing tick must never kill the loop: transient database errors are
        logged and the next tick retries (mirrors ``WorkerLoop.run`` and the
        other scanners). The cursor is only advanced on success, so a failed tick
        re-reads the same events rather than skipping them.
        """
        self._running = True
        logger.info("webhook_event_relay_started")
        try:
            while self._running:
                try:
                    await self.relay_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("webhook_event_relay_tick_error")
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            logger.info("webhook_event_relay_cancelled")
        finally:
            self._running = False
            logger.info("webhook_event_relay_stopped")

    def stop(self) -> None:
        self._running = False

    async def relay_once(self) -> int:
        """Relay one batch of new events. Returns the number of events handled.

        Cursor advance and fan-out share **one transaction**, so the two cannot
        disagree: either an event is recorded as fanned out and the cursor moves
        past it, or neither happens and the next tick retries.

        **Relay lag (commit-order safety).** ``list_after_cursor`` orders by
        ``created_at`` (insert time), but a row only becomes *visible* at commit
        time. A transaction that stamps an early ``created_at`` yet commits after
        the cursor has already advanced past that instant would be skipped
        forever. Holding the cursor ``relay_lag`` seconds behind wall-clock — by
        only relaying events with ``created_at <= now - relay_lag`` — gives such
        late-committing transactions time to land before the cursor reaches them,
        at the cost of a small, bounded notification latency. ``relay_lag=0``
        disables it (immediate relay).
        """
        now = datetime.now(UTC)
        visibility_boundary = now - timedelta(seconds=self._relay_lag) if self._relay_lag else None
        async with self._ctx.admin_db.transaction() as session:
            cursor = self._cursor or await self._resolve_cursor(session)
            events = await EventRepository.list_after_cursor(
                session, cursor, limit=self._batch_limit, before=visibility_boundary
            )
            if not events:
                self._cursor = cursor
                return 0

            queued = 0
            for event in events:
                delivery_ids = await self._fanout.fan_out(
                    session,
                    source_event_id=event.id,
                    event_type=event.type,
                    payload=build_notification_payload(
                        event_id=event.id,
                        event_type=event.type,
                        severity=event.severity,
                        summary=event.summary,
                        data=event.data,
                        created_at=event.created_at.isoformat() if event.created_at else None,
                    ),
                )
                queued += len(delivery_ids)

            last = events[-1]
            self._cursor = (last.created_at, last.id)

        if last.created_at is not None:
            # Lag = how stale the newest relayed event was when we relayed it.
            webhook_metrics.record_relay_lag((now - last.created_at).total_seconds())
        if queued:
            logger.info(
                "webhook_events_relayed",
                events=len(events),
                deliveries_queued=queued,
            )
        return len(events)

    async def _resolve_cursor(self, session: AsyncSession) -> tuple[datetime, str]:
        """Work out where to resume from, without a stored cursor.

        Prefers the newest internal event already relayed, so a restart picks up
        anything emitted while we were down. Falls back to *now* when nothing has
        ever been relayed — a fresh install must not replay history at whatever
        endpoint someone has just created.
        """
        last_relayed_id = await WebhookEventRepository.max_relayed_source_event_id(session)
        if last_relayed_id:
            event = await EventRepository.get_by_id(session, last_relayed_id)
            if event is not None and event.created_at is not None:
                logger.info("webhook_event_relay_resumed", after_event_id=event.id)
                return (event.created_at, event.id)
        # An empty id sorts before any real KSUID, so "now" plus empty id means
        # "strictly events created from this moment on".
        return (datetime.now(UTC), "")
