"""Data access for webhook endpoints, events and deliveries (admin DB).

Three concerns live here, all against the ``admin`` schema:

* **Endpoints** — plain CRUD for configured webhooks.
* **Events** — the deduplicating insert. ``record_event`` returns ``None`` when
  the ``(endpoint_id, source_event_id)`` pair already exists, turning a database
  guarantee into an ordinary "already seen" signal for the caller.
* **Deliveries** — the durable outbound queue: claim a batch of due rows, then
  record each attempt's outcome.

Per the repository rule these are all ``@staticmethod``, take the caller's
``AsyncSession``, and **flush without committing** — the caller owns the
transaction boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.webhook_deliveries import WebhookDelivery
from jentic_one.admin.core.schema.webhook_delivery_attempts import WebhookDeliveryAttempt
from jentic_one.admin.core.schema.webhook_endpoints import WebhookEndpoint
from jentic_one.admin.core.schema.webhook_events import WebhookEvent

# Delivery lifecycle. ``failed`` is retryable (it has a future
# ``next_attempt_at``); ``dead`` is terminal and awaits human inspection.
# ``sending`` is the in-flight lease state: a claimed row sits here with
# ``next_attempt_at`` pushed one lease-window into the future, so a crashed or
# hung dispatcher's row is not immediately reclaimed (which would double-send)
# yet still becomes reclaimable once the lease lapses.
STATUS_PENDING = "pending"
STATUS_SENDING = "sending"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_DEAD = "dead"

# Default delivery lease when a caller does not pass one. Kept here (not only in
# config) so a repo-level caller without a ``WebhookConfig`` still leases safely.
_DEFAULT_LEASE_SECONDS = 60.0

# Defensive cap on the persisted failure reason. ``last_error`` is, in practice,
# always a short closed-set category (see ``delivery._categorize_error``) or a
# fixed internal string, so this never actually truncates today — it is a belt-
# and-braces bound at the storage boundary so a future caller passing a long
# free-text error cannot bloat the column. Applied identically to the parent
# ``last_error`` and the attempt-history ``error`` so the two never diverge.
_MAX_ERROR_LEN = 500


class WebhookEndpointRepository:
    """Read/write access to configured webhook endpoints."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        name: str,
        secret_hash: str,
        secret_encrypted: str,
        target_url: str | None = None,
        event_types: list[str] | None = None,
        allowed_cidrs: list[str] | None = None,
        created_by: str | None = None,
    ) -> WebhookEndpoint:
        endpoint = WebhookEndpoint(
            name=name,
            secret_hash=secret_hash,
            secret_encrypted=secret_encrypted,
            target_url=target_url,
            event_types=event_types or [],
            allowed_cidrs=allowed_cidrs or [],
            created_by=created_by,
        )
        session.add(endpoint)
        await session.flush()
        return endpoint

    @staticmethod
    async def get_by_id(session: AsyncSession, endpoint_id: str) -> WebhookEndpoint | None:
        return await session.get(WebhookEndpoint, endpoint_id)

    @staticmethod
    async def list_all(session: AsyncSession) -> list[WebhookEndpoint]:
        stmt = select(WebhookEndpoint).order_by(WebhookEndpoint.created_at.desc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def list_subscribers(session: AsyncSession, event_type: str) -> list[WebhookEndpoint]:
        """Active endpoints that want ``event_type``.

        An empty ``event_types`` list means "everything". On **Postgres** the
        filter is pushed into the query with JSONB containment
        (``event_types @> '["x"]'``) OR an empty-array match, so only matching
        rows come back — a GIN index on ``event_types`` (see the
        ``…_add_webhook_subscriber_index`` migration) keeps it from scanning the
        whole table on every relayed event. On **SQLite** the JSON column has no
        containment operator, so the historical Python-side filter is kept
        (test/dev volumes are small, and the query stays portable).
        """
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            # ``:et`` is a JSON array literal so ``@>`` (containment) can match a
            # single-element subscription; the empty-array branch is the
            # "everything" catch-all. Bound parameters throughout — never string
            # interpolation into the SQL.
            stmt = (
                select(WebhookEndpoint)
                .where(WebhookEndpoint.active.is_(True))
                .where(
                    text(
                        "(event_types @> cast(:et as jsonb) OR event_types = cast('[]' as jsonb))"
                    ).bindparams(et=f'["{event_type}"]')
                )
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

        stmt = select(WebhookEndpoint).where(WebhookEndpoint.active.is_(True))
        result = await session.execute(stmt)
        return [
            ep
            for ep in result.scalars().all()
            if not ep.event_types or event_type in ep.event_types
        ]

    @staticmethod
    async def update(
        session: AsyncSession,
        endpoint_id: str,
        *,
        name: str | None = None,
        target_url: str | None = None,
        event_types: list[str] | None = None,
        allowed_cidrs: list[str] | None = None,
        active: bool | None = None,
    ) -> WebhookEndpoint | None:
        """Apply a partial update to an endpoint's configuration.

        Only fields passed as non-``None`` are written, which is what makes this
        a PATCH: an omitted field is left exactly as it was. A field passed as an
        empty list (``event_types``/``allowed_cidrs``) *is* written — that is how
        the caller clears a subscription list or an allowlist. Deliberately
        touches no secret column — editing configuration must never affect signing
        authority, which is the separate rotation flow. Returns ``None`` when the
        endpoint does not exist so the caller can raise the not-found error.
        """
        endpoint = await session.get(WebhookEndpoint, endpoint_id)
        if endpoint is None:
            return None
        if name is not None:
            endpoint.name = name
        if target_url is not None:
            endpoint.target_url = target_url
        if event_types is not None:
            endpoint.event_types = event_types
        if allowed_cidrs is not None:
            endpoint.allowed_cidrs = allowed_cidrs
        if active is not None:
            endpoint.active = active
        await session.flush()
        return endpoint

    @staticmethod
    async def deactivate(session: AsyncSession, endpoint_id: str) -> None:
        """Mark an endpoint inactive — used when a target answers ``410 Gone``."""
        await session.execute(
            update(WebhookEndpoint).where(WebhookEndpoint.id == endpoint_id).values(active=False)
        )
        await session.flush()

    @staticmethod
    async def delete(session: AsyncSession, endpoint_id: str) -> bool:
        endpoint = await session.get(WebhookEndpoint, endpoint_id)
        if endpoint is None:
            return False
        await session.delete(endpoint)
        await session.flush()
        return True


class WebhookEventRepository:
    """Write/read access to accepted webhook events."""

    @staticmethod
    async def record_event(
        session: AsyncSession,
        *,
        endpoint_id: str,
        source_event_id: str,
        event_type: str,
        payload: dict[str, Any],
        created_by: str | None = None,
    ) -> WebhookEvent | None:
        """Insert an accepted event, or return ``None`` if already recorded.

        Deduplication is enforced by the unique
        ``(endpoint_id, source_event_id)`` constraint rather than a
        check-then-insert, which would race under concurrent retries. The
        ``IntegrityError`` is caught and reported as ``None`` so the caller can
        answer ``200`` — a duplicate delivery is the sender behaving correctly,
        not an error.

        The failed INSERT poisons the transaction, so the savepoint
        (``begin_nested``) is what lets the caller carry on using the session
        afterwards.
        """
        try:
            async with session.begin_nested():
                event = WebhookEvent(
                    endpoint_id=endpoint_id,
                    source_event_id=source_event_id,
                    event_type=event_type,
                    payload=payload,
                    created_by=created_by,
                )
                session.add(event)
                await session.flush()
                return event
        except IntegrityError:
            return None

    @staticmethod
    async def get_by_id(session: AsyncSession, event_id: str) -> WebhookEvent | None:
        return await session.get(WebhookEvent, event_id)

    @staticmethod
    async def max_relayed_source_event_id(session: AsyncSession) -> str | None:
        """Highest internal-event id already relayed to an endpoint.

        This *is* the relay's durable cursor, derived rather than stored: because
        ``source_event_id`` holds the internal ``events.id`` (a K-sortable KSUID),
        the maximum is the newest event we have already fanned out. Deriving it
        avoids a separate cursor table and cannot drift from reality, and it means
        a restart resumes where the relay left off instead of silently skipping
        events emitted while the process was down.

        Returns ``None`` when nothing has been relayed yet, which the caller
        treats as "start from now" rather than replaying all history.
        """
        result = await session.execute(select(func.max(WebhookEvent.source_event_id)))
        return result.scalar_one_or_none()


class WebhookDeliveryRepository:
    """The durable outbound delivery queue."""

    @staticmethod
    async def enqueue(
        session: AsyncSession,
        *,
        event_id: str,
        endpoint_id: str,
        created_by: str | None = None,
    ) -> WebhookDelivery:
        """Queue one delivery, due immediately."""
        delivery = WebhookDelivery(
            event_id=event_id,
            endpoint_id=endpoint_id,
            status=STATUS_PENDING,
            next_attempt_at=datetime.now(UTC),
            created_by=created_by,
        )
        session.add(delivery)
        await session.flush()
        return delivery

    @staticmethod
    async def claim_due(
        session: AsyncSession,
        *,
        limit: int = 10,
        now: datetime | None = None,
        lease_s: float = _DEFAULT_LEASE_SECONDS,
        max_in_flight_per_endpoint: int = 1,
    ) -> list[WebhookDelivery]:
        """Claim up to ``limit`` deliveries whose time has come, taking a lease.

        ``FOR UPDATE SKIP LOCKED`` is the same pattern the job ``WorkerLoop``
        uses: several dispatchers can claim disjoint batches concurrently without
        blocking each other. A claimed row is moved to ``sending`` (the lease
        state) with ``next_attempt_at`` pushed ``lease_s`` into the future. That
        closes two holes in the previous claim-time semantics:

        * **No duplicate send.** A crashed/hung dispatcher's row is not
          immediately reclaimable — it stays invisible until the lease lapses,
          at which point another dispatcher may safely retry it.
        * **No burned attempts.** ``attempt_count`` is **not** bumped here; it is
          bumped only when a *real* attempt outcome is recorded
          (``mark_succeeded`` / ``mark_failed``), so a crash loop between claim
          and send does not exhaust the retry budget.

        ``max_in_flight_per_endpoint`` caps how many deliveries may be
        **in flight** to any one ``endpoint_id`` at once (0 = uncapped) so a
        fleet of dispatchers cannot accidentally DoS one customer. The cap is
        enforced across dispatchers, not merely within a single batch: before
        claiming, we count the rows already leased to each endpoint (``sending``
        with a *live* lease, i.e. ``next_attempt_at > now``) and subtract them
        from that endpoint's budget, so ``existing live leases + newly claimed``
        never exceeds the cap even when M dispatchers claim concurrently. The
        ``FOR UPDATE SKIP LOCKED`` claim plus the live-lease pre-count are what
        make this hold under real concurrency: a row another dispatcher is
        actively sending sits in ``sending`` with a future ``next_attempt_at``,
        is invisible to the candidate scan, and is counted against the budget
        here. Both the Postgres and the portable (SQLite) paths apply the same
        per-endpoint budgeting in Python over the ordered candidates.

        The caller must **commit and release the session before sending** —
        holding a session across the outbound POST would exhaust the small
        connection pool on a slow destination.
        """
        moment = now or datetime.now(UTC)
        cap = max_in_flight_per_endpoint

        # Existing in-flight leases per endpoint: ``sending`` rows whose lease is
        # still live (``next_attempt_at > moment``) are being sent by some
        # dispatcher right now, so they count against the cap even though they
        # are invisible to the candidate scan below (which only sees rows *due*,
        # i.e. ``next_attempt_at <= moment``). Counting them here is what makes
        # the cap hold *across* dispatchers rather than only within one batch.
        # Skipped entirely when the cap is disabled (``cap <= 0``).
        in_flight: dict[str, int] = {}
        if cap and cap > 0:
            live = await session.execute(
                select(WebhookDelivery.endpoint_id, func.count())
                .where(
                    WebhookDelivery.status == STATUS_SENDING,
                    WebhookDelivery.next_attempt_at > moment,
                )
                .group_by(WebhookDelivery.endpoint_id)
            )
            in_flight = {row[0]: int(row[1]) for row in live.all()}

        # Over-select the ordered due rows so the per-endpoint budgeting below
        # stays honest when many rows share one endpoint, then lease only the
        # survivors. ``FOR UPDATE SKIP LOCKED`` lets concurrent dispatchers claim
        # disjoint batches; combined with the live-lease pre-count above, the
        # effective per-endpoint in-flight count respects the cap across the
        # whole fleet, not just within this batch.
        overselect = limit if not cap else max(limit, limit * max(cap, 1))
        stmt = (
            select(WebhookDelivery)
            .where(
                WebhookDelivery.status.in_((STATUS_PENDING, STATUS_FAILED, STATUS_SENDING)),
                WebhookDelivery.next_attempt_at <= moment,
            )
            .order_by(WebhookDelivery.next_attempt_at)
            .limit(overselect)
            .with_for_update(skip_locked=True)
        )
        result = await session.execute(stmt)
        candidates = list(result.scalars().all())

        selected: list[WebhookDelivery] = []
        # Seed each endpoint's running tally with the leases already in flight so
        # the budget is (cap - existing) rather than a fresh cap per batch.
        per_endpoint: dict[str, int] = dict(in_flight)
        for delivery in candidates:
            if cap and cap > 0:
                seen = per_endpoint.get(delivery.endpoint_id, 0)
                if seen >= cap:
                    continue
                per_endpoint[delivery.endpoint_id] = seen + 1
            selected.append(delivery)
            if len(selected) >= limit:
                break

        return await WebhookDeliveryRepository._apply_lease(
            session, selected, moment=moment, lease_s=lease_s
        )

    @staticmethod
    async def _apply_lease(
        session: AsyncSession,
        claimed: list[WebhookDelivery],
        *,
        moment: datetime,
        lease_s: float,
    ) -> list[WebhookDelivery]:
        """Move claimed rows to the ``sending`` lease and push their visibility.

        ``attempt_count`` is intentionally left untouched — see ``claim_due``.
        """
        deadline = moment + timedelta(seconds=lease_s)
        for delivery in claimed:
            delivery.status = STATUS_SENDING
            delivery.next_attempt_at = deadline
        await session.flush()
        return claimed

    @staticmethod
    async def mark_succeeded(
        session: AsyncSession,
        delivery_id: str,
        *,
        status_code: int,
        duration_ms: int | None = None,
        attempt_number: int | None = None,
    ) -> None:
        """Record a successful attempt.

        Bumps ``attempt_count`` and stamps ``last_attempt_at`` here — at the
        *real* outcome — rather than at claim time, so the counter reflects
        attempts actually made against the receiver (see ``claim_due``). Persists
        ``duration_ms`` (this attempt's wall-clock) on the parent for the
        response-time view, and appends a ``webhook_delivery_attempts`` history
        row when ``attempt_number`` is supplied.
        """
        now = datetime.now(UTC)
        await session.execute(
            update(WebhookDelivery)
            .where(WebhookDelivery.id == delivery_id)
            .values(
                status=STATUS_SUCCEEDED,
                last_status_code=status_code,
                last_error=None,
                next_attempt_at=None,
                last_attempt_at=now,
                duration_ms=duration_ms,
                attempt_count=WebhookDelivery.attempt_count + 1,
            )
        )
        await WebhookDeliveryRepository._record_attempt(
            session,
            delivery_id=delivery_id,
            attempt_number=attempt_number,
            status_code=status_code,
            error=None,
            duration_ms=duration_ms,
        )
        await session.flush()

    @staticmethod
    async def mark_failed(
        session: AsyncSession,
        delivery_id: str,
        *,
        status_code: int | None,
        error: str,
        retry_in: timedelta | None,
        duration_ms: int | None = None,
        attempt_number: int | None = None,
    ) -> None:
        """Record a failed attempt; ``retry_in=None`` dead-letters the row.

        The backoff schedule is the caller's decision (it knows the attempt
        count and the configured cap) — this method only persists the outcome.
        Like ``mark_succeeded`` it bumps ``attempt_count`` and stamps
        ``last_attempt_at`` here, at the real outcome, so a claim that never
        reaches a send (crash between claim and POST) does not burn an attempt.
        Persists ``duration_ms`` and appends a history row when
        ``attempt_number`` is supplied.
        """
        now = datetime.now(UTC)
        values: dict[str, Any] = {
            "last_status_code": status_code,
            "last_error": error[:_MAX_ERROR_LEN],
            "last_attempt_at": now,
            "duration_ms": duration_ms,
            "attempt_count": WebhookDelivery.attempt_count + 1,
        }
        if retry_in is None:
            values["status"] = STATUS_DEAD
            values["next_attempt_at"] = None
        else:
            values["status"] = STATUS_FAILED
            values["next_attempt_at"] = now + retry_in
        await session.execute(
            update(WebhookDelivery).where(WebhookDelivery.id == delivery_id).values(**values)
        )
        await WebhookDeliveryRepository._record_attempt(
            session,
            delivery_id=delivery_id,
            attempt_number=attempt_number,
            status_code=status_code,
            error=error[:_MAX_ERROR_LEN],
            duration_ms=duration_ms,
        )
        await session.flush()

    @staticmethod
    async def _record_attempt(
        session: AsyncSession,
        *,
        delivery_id: str,
        attempt_number: int | None,
        status_code: int | None,
        error: str | None,
        duration_ms: int | None,
    ) -> None:
        """Append a per-attempt history row (no-op when the ordinal is unknown).

        The dispatcher always supplies ``attempt_number``; the parameter stays
        optional so the legacy repo callers (and tests that only assert parent
        state) do not have to synthesise an ordinal. Without an ordinal there is
        nothing meaningful to record, so we skip rather than guess.
        """
        if attempt_number is None:
            return
        session.add(
            WebhookDeliveryAttempt(
                delivery_id=delivery_id,
                attempt_number=attempt_number,
                status_code=status_code,
                error=error,
                duration_ms=duration_ms,
            )
        )
        await session.flush()

    @staticmethod
    async def list_attempts(
        session: AsyncSession, delivery_id: str, *, limit: int = 50
    ) -> list[WebhookDeliveryAttempt]:
        """Per-attempt history for one delivery, newest first."""
        result = await session.execute(
            select(WebhookDeliveryAttempt)
            .where(WebhookDeliveryAttempt.delivery_id == delivery_id)
            .order_by(WebhookDeliveryAttempt.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_for_endpoint(
        session: AsyncSession, endpoint_id: str, *, limit: int = 50
    ) -> list[WebhookDelivery]:
        result = await session.execute(
            select(WebhookDelivery)
            .where(WebhookDelivery.endpoint_id == endpoint_id)
            .order_by(WebhookDelivery.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def aggregate_for_endpoint(
        session: AsyncSession,
        endpoint_id: str,
        *,
        now: datetime | None = None,
        window: timedelta = timedelta(hours=24),
    ) -> dict[str, Any]:
        """Summarise an endpoint's delivery health for the drawer Overview.

        Returns counts by status (all-time), a last-24h total + failure count,
        the most-recent attempt's status code / timestamp, the next scheduled
        attempt across live rows, and the average attempt duration (ms) computed
        across **all** recorded attempts (``webhook_delivery_attempts``), so a
        retried delivery contributes every attempt's latency rather than only its
        last. The remaining figures derive from ``webhook_deliveries`` in a
        couple of grouped queries. The ``window`` is parameterised so tests can
        pin it.
        """
        moment = now or datetime.now(UTC)
        window_start = moment - window

        # All-time counts grouped by status.
        status_rows = await session.execute(
            select(WebhookDelivery.status, func.count())
            .where(WebhookDelivery.endpoint_id == endpoint_id)
            .group_by(WebhookDelivery.status)
        )
        counts_by_status: dict[str, int] = {row[0]: row[1] for row in status_rows.all()}

        # Last-window activity: total + how many are in a non-success terminal
        # or retrying state (failed/dead), keyed off the last attempt time.
        recent_total = (
            await session.execute(
                select(func.count())
                .select_from(WebhookDelivery)
                .where(
                    WebhookDelivery.endpoint_id == endpoint_id,
                    WebhookDelivery.last_attempt_at.is_not(None),
                    WebhookDelivery.last_attempt_at >= window_start,
                )
            )
        ).scalar_one()
        recent_failed = (
            await session.execute(
                select(func.count())
                .select_from(WebhookDelivery)
                .where(
                    WebhookDelivery.endpoint_id == endpoint_id,
                    WebhookDelivery.last_attempt_at.is_not(None),
                    WebhookDelivery.last_attempt_at >= window_start,
                    WebhookDelivery.status.in_((STATUS_FAILED, STATUS_DEAD)),
                )
            )
        ).scalar_one()

        # Most-recent attempt: status code + when.
        last_row = (
            await session.execute(
                select(
                    WebhookDelivery.last_status_code,
                    WebhookDelivery.last_attempt_at,
                    WebhookDelivery.duration_ms,
                )
                .where(
                    WebhookDelivery.endpoint_id == endpoint_id,
                    WebhookDelivery.last_attempt_at.is_not(None),
                )
                .order_by(WebhookDelivery.last_attempt_at.desc())
                .limit(1)
            )
        ).first()

        # Next scheduled attempt across live (pending/failed/sending) rows.
        next_attempt_at = (
            await session.execute(
                select(func.min(WebhookDelivery.next_attempt_at)).where(
                    WebhookDelivery.endpoint_id == endpoint_id,
                    WebhookDelivery.status.in_((STATUS_PENDING, STATUS_FAILED, STATUS_SENDING)),
                    WebhookDelivery.next_attempt_at.is_not(None),
                )
            )
        ).scalar_one_or_none()

        # Average response time across **every** recorded attempt for this
        # endpoint's deliveries — not the parent's ``duration_ms`` (which is
        # overwritten each attempt and so only holds the *latest* attempt's
        # figure, misrepresenting latency whenever a delivery retried). Joining
        # the per-attempt history (``webhook_delivery_attempts``) to the parent
        # deliveries filtered by ``endpoint_id`` averages all real attempts.
        # Kept all-time (not windowed) to match the all-time ``counts_by_status``
        # / ``last_duration_ms`` figures above; the 24h fields are explicitly the
        # ``recent_*`` ones.
        avg_duration_ms = (
            await session.execute(
                select(func.avg(WebhookDeliveryAttempt.duration_ms))
                .select_from(WebhookDeliveryAttempt)
                .join(
                    WebhookDelivery,
                    WebhookDelivery.id == WebhookDeliveryAttempt.delivery_id,
                )
                .where(
                    WebhookDelivery.endpoint_id == endpoint_id,
                    WebhookDeliveryAttempt.duration_ms.is_not(None),
                )
            )
        ).scalar_one_or_none()

        total = sum(counts_by_status.values())
        return {
            "total": total,
            "counts_by_status": counts_by_status,
            "recent_total": int(recent_total),
            "recent_failed": int(recent_failed),
            "last_status_code": last_row[0] if last_row else None,
            "last_attempt_at": last_row[1] if last_row else None,
            "last_duration_ms": last_row[2] if last_row else None,
            "next_attempt_at": next_attempt_at,
            "avg_duration_ms": float(avg_duration_ms) if avg_duration_ms is not None else None,
        }

    @staticmethod
    async def reset_for_resend(session: AsyncSession, delivery_id: str) -> bool:
        """Make a finished (or dead) delivery due again — the manual retry.

        Resets ``attempt_count`` to 0 as well: without that a resent *dead* row
        keeps its exhausted count, gets exactly one more try, and immediately
        re-dead-letters — defeating the point of a manual resend (the operator
        fixed the receiver and wants the full retry budget back).
        """
        delivery = await session.get(WebhookDelivery, delivery_id)
        if delivery is None:
            return False
        delivery.status = STATUS_PENDING
        delivery.next_attempt_at = datetime.now(UTC)
        delivery.last_error = None
        delivery.attempt_count = 0
        await session.flush()
        return True

    @staticmethod
    async def prune_succeeded(
        session: AsyncSession,
        *,
        older_than: datetime,
        limit: int = 1000,
    ) -> int:
        """Delete up to ``limit`` ``succeeded`` deliveries older than ``older_than``.

        Retention only ever reaps *succeeded* rows: a ``dead`` row is a
        dead-letter kept for inspection/resend until an operator acknowledges it,
        and a ``pending``/``failed``/``sending`` row is still live. Deleting in
        bounded batches (``limit``) keeps one sweep from taking a long table lock.
        Returns the number of rows deleted (0 when nothing was due).
        """
        subq = (
            select(WebhookDelivery.id)
            .where(
                WebhookDelivery.status == STATUS_SUCCEEDED,
                WebhookDelivery.last_attempt_at.is_not(None),
                WebhookDelivery.last_attempt_at < older_than,
            )
            .order_by(WebhookDelivery.last_attempt_at)
            .limit(limit)
        )
        ids = list((await session.execute(subq)).scalars().all())
        if not ids:
            return 0
        await session.execute(delete(WebhookDelivery).where(WebhookDelivery.id.in_(ids)))
        await session.flush()
        return len(ids)
