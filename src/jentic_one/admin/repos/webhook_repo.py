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
        created_by: str | None = None,
    ) -> WebhookEndpoint:
        endpoint = WebhookEndpoint(
            name=name,
            secret_hash=secret_hash,
            secret_encrypted=secret_encrypted,
            target_url=target_url,
            event_types=event_types or [],
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
        active: bool | None = None,
    ) -> WebhookEndpoint | None:
        """Apply a partial update to an endpoint's configuration.

        Only fields passed as non-``None`` are written, which is what makes this
        a PATCH: an omitted field is left exactly as it was. Deliberately touches
        no secret column — editing configuration must never affect signing
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

        ``max_in_flight_per_endpoint`` caps how many rows are claimed for any one
        ``endpoint_id`` in a single batch (0 = uncapped), so a fleet of
        dispatchers cannot accidentally DoS one customer. On Postgres this uses a
        ``DISTINCT ON (endpoint_id)`` pre-selection; on SQLite (no
        ``DISTINCT ON``) it is enforced with an in-Python per-endpoint tally over
        the ordered candidates.

        The caller must **commit and release the session before sending** —
        holding a session across the outbound POST would exhaust the small
        connection pool on a slow destination.
        """
        moment = now or datetime.now(UTC)
        cap = max_in_flight_per_endpoint
        is_pg = session.bind is not None and session.bind.dialect.name == "postgresql"

        # DISTINCT ON keeps only the earliest-due row per endpoint, so a single
        # batch never claims more than one row for any endpoint when cap == 1.
        # (cap > 1 falls through to the Python tally below, which also handles
        # it — DISTINCT ON expresses only the cap==1 case.)
        if is_pg and cap == 1:
            inner = (
                select(WebhookDelivery.id)
                .where(
                    WebhookDelivery.status.in_((STATUS_PENDING, STATUS_FAILED, STATUS_SENDING)),
                    WebhookDelivery.next_attempt_at <= moment,
                )
                .order_by(
                    WebhookDelivery.endpoint_id,
                    WebhookDelivery.next_attempt_at,
                )
                .distinct(WebhookDelivery.endpoint_id)
            )
            stmt = (
                select(WebhookDelivery)
                .where(WebhookDelivery.id.in_(inner.scalar_subquery()))
                .order_by(WebhookDelivery.next_attempt_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            result = await session.execute(stmt)
            claimed = list(result.scalars().all())
            return await WebhookDeliveryRepository._apply_lease(
                session, claimed, moment=moment, lease_s=lease_s
            )

        # Portable path (SQLite, or Postgres with cap != 1): over-select the
        # ordered due rows, then apply the per-endpoint cap in Python before
        # leasing only the survivors. Over-selection keeps the cap honest when
        # many rows share one endpoint.
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
        per_endpoint: dict[str, int] = {}
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
    async def mark_succeeded(session: AsyncSession, delivery_id: str, *, status_code: int) -> None:
        """Record a successful attempt.

        Bumps ``attempt_count`` and stamps ``last_attempt_at`` here — at the
        *real* outcome — rather than at claim time, so the counter reflects
        attempts actually made against the receiver (see ``claim_due``).
        """
        await session.execute(
            update(WebhookDelivery)
            .where(WebhookDelivery.id == delivery_id)
            .values(
                status=STATUS_SUCCEEDED,
                last_status_code=status_code,
                last_error=None,
                next_attempt_at=None,
                last_attempt_at=datetime.now(UTC),
                attempt_count=WebhookDelivery.attempt_count + 1,
            )
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
    ) -> None:
        """Record a failed attempt; ``retry_in=None`` dead-letters the row.

        The backoff schedule is the caller's decision (it knows the attempt
        count and the configured cap) — this method only persists the outcome.
        Like ``mark_succeeded`` it bumps ``attempt_count`` and stamps
        ``last_attempt_at`` here, at the real outcome, so a claim that never
        reaches a send (crash between claim and POST) does not burn an attempt.
        """
        values: dict[str, Any] = {
            "last_status_code": status_code,
            "last_error": error[:1000],
            "last_attempt_at": datetime.now(UTC),
            "attempt_count": WebhookDelivery.attempt_count + 1,
        }
        if retry_in is None:
            values["status"] = STATUS_DEAD
            values["next_attempt_at"] = None
        else:
            values["status"] = STATUS_FAILED
            values["next_attempt_at"] = datetime.now(UTC) + retry_in
        await session.execute(
            update(WebhookDelivery).where(WebhookDelivery.id == delivery_id).values(**values)
        )
        await session.flush()

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
