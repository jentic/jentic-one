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

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.webhook_deliveries import WebhookDelivery
from jentic_one.admin.core.schema.webhook_endpoints import WebhookEndpoint
from jentic_one.admin.core.schema.webhook_events import WebhookEvent

# Delivery lifecycle. ``failed`` is retryable (it has a future
# ``next_attempt_at``); ``dead`` is terminal and awaits human inspection.
STATUS_PENDING = "pending"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_DEAD = "dead"


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

        An empty ``event_types`` list means "everything", so the JSON filter is
        applied in Python: the column is a JSON variant (JSONB on Postgres,
        JSON on SQLite) and containment syntax differs between them. Subscriber
        counts are small, so this costs nothing and keeps the query portable.
        """
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
        session: AsyncSession, *, limit: int = 10, now: datetime | None = None
    ) -> list[WebhookDelivery]:
        """Claim up to ``limit`` deliveries whose time has come.

        ``FOR UPDATE SKIP LOCKED`` is the same pattern the job ``WorkerLoop``
        uses: several workers can claim disjoint batches concurrently without
        blocking each other. Rows are marked ``pending`` with the attempt
        counter incremented, so a crash mid-send leaves the row retryable
        rather than lost.

        The caller must **commit and release the session before sending** —
        holding a session across the outbound POST would exhaust the small
        connection pool on a slow destination.
        """
        moment = now or datetime.now(UTC)
        stmt = (
            select(WebhookDelivery)
            .where(
                WebhookDelivery.status.in_((STATUS_PENDING, STATUS_FAILED)),
                WebhookDelivery.next_attempt_at <= moment,
            )
            .order_by(WebhookDelivery.next_attempt_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await session.execute(stmt)
        claimed = list(result.scalars().all())
        for delivery in claimed:
            delivery.attempt_count += 1
            delivery.last_attempt_at = moment
            delivery.status = STATUS_PENDING
        await session.flush()
        return claimed

    @staticmethod
    async def mark_succeeded(session: AsyncSession, delivery_id: str, *, status_code: int) -> None:
        await session.execute(
            update(WebhookDelivery)
            .where(WebhookDelivery.id == delivery_id)
            .values(
                status=STATUS_SUCCEEDED,
                last_status_code=status_code,
                last_error=None,
                next_attempt_at=None,
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
        """
        values: dict[str, Any] = {
            "last_status_code": status_code,
            "last_error": error[:1000],
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
        """Make a finished (or dead) delivery due again — the manual retry."""
        delivery = await session.get(WebhookDelivery, delivery_id)
        if delivery is None:
            return False
        delivery.status = STATUS_PENDING
        delivery.next_attempt_at = datetime.now(UTC)
        delivery.last_error = None
        await session.flush()
        return True
