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
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.repos.event_repo import EventRepository
from jentic_one.admin.repos.webhook_repo import WebhookEventRepository
from jentic_one.admin.services.webhooks.fanout import (
    WebhookFanoutService,
    build_notification_payload,
)

if TYPE_CHECKING:
    from jentic_one.shared.context import Context

logger = structlog.get_logger(__name__)

_POLL_INTERVAL_SECONDS = 2.0
_BATCH_LIMIT = 100


class InternalEventRelay:
    """Periodically fans newly emitted internal events out to subscribers."""

    def __init__(
        self,
        ctx: Context,
        *,
        poll_interval: float = _POLL_INTERVAL_SECONDS,
        batch_limit: int = _BATCH_LIMIT,
    ) -> None:
        self._ctx = ctx
        self._fanout = WebhookFanoutService(ctx)
        self._poll_interval = poll_interval
        self._batch_limit = batch_limit
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
        """
        async with self._ctx.admin_db.transaction() as session:
            cursor = self._cursor or await self._resolve_cursor(session)
            events = await EventRepository.list_after_cursor(
                session, cursor, limit=self._batch_limit
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
