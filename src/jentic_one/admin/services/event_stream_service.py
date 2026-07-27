"""Event streaming service."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

from jentic_one.admin.repos import EventRepository
from jentic_one.admin.services.schemas.events import EventView, Heartbeat
from jentic_one.shared.context import Context
from jentic_one.shared.models.events import EventSeverity

# Rows fetched per repo page while draining the overlap window. A short page
# (< limit) means the window is drained, saving the empty-batch round-trip.
_PAGE_LIMIT = 100


class EventStreamService:
    """Polls for new events and yields them as a transport-agnostic async stream."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def stream(
        self,
        since: datetime | None = None,
        last_event_id: str | None = None,
        poll_interval_seconds: float = 5.0,
        overlap_seconds: float = 15.0,
        event_type: list[str] | None = None,
        severity: list[str] | None = None,
        requires_action: bool | None = None,
        trace_id: str | None = None,
        actor_id: str | None = None,
        actor_type: str | None = None,
    ) -> AsyncIterator[EventView | Heartbeat]:
        """Yield new events (and heartbeats when idle) forever.

        Visibility-race note (the reason for ``overlap_seconds``)
        ----------------------------------------------------------
        ``created_at`` is stamped when the ORM flushes the row, but the row only
        becomes visible to this poller once the WRITER's transaction commits —
        which can be noticeably later (an event emitted mid-transaction, e.g.
        ``agent.self_registered`` inside the DCR ``/register`` transaction, is
        stamped early and committed at the end). A strict high-watermark poll
        (``created_at > newest_seen``) therefore PERMANENTLY skips any event
        whose commit lands after the watermark has moved past its timestamp —
        the rail then never shows it until a full page refresh.

        Instead, every poll re-queries from ``watermark - overlap`` and dedups
        per-connection by event id, so a late-committing row is picked up on a
        subsequent poll as long as its commit lag (plus writer/poller clock
        skew) stays under the overlap. Each event is yielded at most once per
        connection; the paging loop advances a (created_at, id) cursor so bursts
        larger than one repo page can't wedge the poll.

        Delivery guarantee: at-least-once ACROSS connections (a reconnect may
        re-receive events near its resume point — clients dedup by SSE id),
        exactly-once WITHIN a connection. Rows already visible at connect time
        that sit at-or-before the resume point are pre-seeded into the dedup map
        below, so the overlap never replays history the caller didn't ask for —
        only rows whose COMMIT lands after connect are rescued by it. Late
        commits are rescued for up to ``overlap_seconds`` of lag + skew; beyond
        that they are lost to the live stream (the durable backlog fetch still
        shows them).
        """
        overlap = timedelta(seconds=overlap_seconds)
        # FastAPI parses an offset-less ``?since=`` as a NAIVE datetime; the ORM
        # returns aware-UTC rows, and naive-vs-aware comparison raises TypeError
        # (killing the stream mid-SSE). Column values are UTC, so pin it.
        if since is not None and since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        # Clamp a future ``since`` (fast client clock) to the present —
        # symmetric with the watermark-advance clamp below. Un-clamped, the
        # horizon would sit in the future and every normally-stamped event
        # until the clock caught up would be seeded as "history" and dropped.
        if since is not None:
            since = min(since, datetime.now(UTC))
        # Newest created_at yielded so far (or the caller's resume point).
        watermark: datetime
        # Ids already yielded on THIS connection, kept for the overlap horizon.
        seen: dict[str, datetime] = {}

        watermark = since if since is not None else datetime.now(UTC)
        resume_id: str | None = None
        if last_event_id is not None:
            async with self._ctx.admin_db.session() as session:
                event = await EventRepository.get_by_id(session, last_event_id)
            if event is not None:
                # Same future-clamp as ``since``: a future-stamped resume row
                # must not black-hole the stream. When clamped, the id
                # tie-break below simply never fires and rows near the resume
                # point are re-delivered — allowed (at-least-once across
                # connections; clients dedup by SSE id).
                watermark = min(event.created_at, datetime.now(UTC))
                resume_id = event.id

        # Pre-seed the dedup map with rows ALREADY VISIBLE at-or-before the
        # resume point. Without this, the first poll's overlap window replays up
        # to ``overlap_seconds`` of history on every fresh connect (re-firing
        # client-side toasts/invalidations on page load). Rows seeded here are
        # by definition not late-commit victims for THIS connection; anything
        # that becomes visible after this scan is delivered normally.
        page_cursor: tuple[datetime, str] = (watermark - overlap, "")
        while True:
            async with self._ctx.admin_db.session() as session:
                batch = await EventRepository.list_after_cursor(
                    session,
                    page_cursor,
                    limit=_PAGE_LIMIT,
                    event_type=event_type,
                    severity=severity,
                    requires_action=requires_action,
                    trace_id=trace_id,
                    actor_id=actor_id,
                    actor_type=actor_type,
                )
            for event in batch:
                # ``since``/fresh connects resume EXCLUSIVELY after the
                # timestamp; a ``Last-Event-ID`` resume point also skips
                # same-instant siblings up to and including that id.
                before_resume = (
                    (event.created_at, event.id) <= (watermark, resume_id)
                    if resume_id is not None and event.created_at == watermark
                    else event.created_at <= watermark
                )
                if before_resume:
                    seen[event.id] = event.created_at
            # Rows past the watermark can never satisfy ``before_resume``, so
            # once a page's tail crosses it the scan is done. Without this
            # bound, an old resume point (a tab waking after hours) would page
            # the ENTIRE backlog to the present before the first yield — pure
            # wasted I/O the main loop then re-reads to deliver.
            if len(batch) < _PAGE_LIMIT or batch[-1].created_at > watermark:
                break
            page_cursor = (batch[-1].created_at, batch[-1].id)

        while True:
            yielded = False
            # "" sorts before every real id, so the two-tuple cursor starts
            # strictly at the horizon without skipping same-instant rows.
            page_cursor = (watermark - overlap, "")

            while True:
                # The DB session is strictly scoped to this ``async with`` block
                # and never held across a yield or the ``asyncio.sleep`` below.
                # Keeping the session local (never stashed on ``self``)
                # guarantees that a ``CancelledError`` raised on SSE client
                # disconnect unwinds the context manager and returns the pooled
                # connection to the engine — the leak guarded against in #627.
                async with self._ctx.admin_db.session() as session:
                    batch = await EventRepository.list_after_cursor(
                        session,
                        page_cursor,
                        limit=_PAGE_LIMIT,
                        event_type=event_type,
                        severity=severity,
                        requires_action=requires_action,
                        trace_id=trace_id,
                        actor_id=actor_id,
                        actor_type=actor_type,
                    )
                for event in batch:
                    if event.id in seen:
                        continue
                    seen[event.id] = event.created_at
                    if event.created_at > watermark:
                        # Clamp the advance to wall-clock: one future-stamped
                        # row (writer clock skew, bad backfill) must not push
                        # the overlap horizon past the present, which would
                        # black-hole every normally-stamped event until the
                        # clock catches up.
                        watermark = max(watermark, min(event.created_at, datetime.now(UTC)))
                    yielded = True
                    yield EventView(
                        id=event.id,
                        type=event.type,
                        severity=EventSeverity(event.severity),
                        summary=event.summary,
                        requires_action=event.requires_action,
                        acknowledged=event.acknowledged,
                        acknowledged_at=event.acknowledged_at,
                        acknowledged_by=event.acknowledged_by,
                        trace_id=event.trace_id,
                        detail=event.detail,
                        data=event.data,
                        execution_id=event.execution_id,
                        job_id=event.job_id,
                        actor_id=event.actor_id,
                        actor_type=event.actor_type,
                        created_at=event.created_at,
                    )
                # A short page means the window is drained — skip the extra
                # round-trip that would only observe emptiness.
                if len(batch) < _PAGE_LIMIT:
                    break
                page_cursor = (batch[-1].created_at, batch[-1].id)

            if not yielded:
                yield Heartbeat(sent_at=datetime.now(UTC))

            # Drop dedup entries that fell behind the overlap horizon — the
            # query can no longer return them, so the map stays bounded.
            horizon = watermark - overlap
            seen = {id_: ts for id_, ts in seen.items() if ts >= horizon}

            await asyncio.sleep(poll_interval_seconds)
