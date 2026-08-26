"""Integration tests for webhook fan-out and the internal-event relay.

Covers the second half of the notification path: a real row in the ``events``
table becoming queued deliveries for exactly the endpoints that should get them.
Real Postgres throughout — the deduplication and cursor behaviour being tested
are database guarantees, not Python ones.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.webhook_deliveries import WebhookDelivery
from jentic_one.admin.core.schema.webhook_endpoints import WebhookEndpoint
from jentic_one.admin.core.schema.webhook_events import WebhookEvent
from jentic_one.admin.repos.event_repo import EventRepository
from jentic_one.admin.repos.webhook_repo import (
    WebhookDeliveryRepository,
    WebhookEndpointRepository,
    WebhookEventRepository,
)
from jentic_one.admin.services.webhooks.fanout import (
    WebhookFanoutService,
    build_notification_payload,
)
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.jobs.execution_handler import ExecutionHandler
from jentic_one.shared.jobs.protocols import (
    UpstreamExecRequest,
    UpstreamExecResult,
    UpstreamExecutor,
)
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.webhooks.relay import InternalEventRelay

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_all(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Clear webhook tables *and* events, so cursors start from a known place."""

    async def _wipe() -> None:
        async with admin_db.session() as session:
            await session.execute(delete(WebhookDelivery))
            await session.execute(delete(WebhookEvent))
            await session.execute(delete(WebhookEndpoint))
            await session.execute(delete(Event))
            await session.commit()

    await _wipe()
    yield
    await _wipe()


async def _endpoint(
    admin_db: DatabaseSession,
    *,
    name: str,
    event_types: list[str] | None = None,
    target_url: str | None = "https://receiver.test/hook",
    active: bool = True,
) -> str:
    async with admin_db.transaction() as session:
        endpoint = await WebhookEndpointRepository.create(
            session,
            name=name,
            secret_hash="hashed",  # pragma: allowlist secret
            secret_encrypted="encrypted-blob",  # pragma: allowlist secret
            target_url=target_url,
            event_types=event_types or [],
            created_by="test",
        )
        endpoint_id = endpoint.id
        if not active:
            await WebhookEndpointRepository.deactivate(session, endpoint_id)
        return endpoint_id


async def _emit(admin_db: DatabaseSession, event_type: str, **data: object) -> str:
    """Write a real row to the events table, as the platform would."""
    async with admin_db.transaction() as session:
        event = await EventRepository.create(
            session,
            type=event_type,
            severity=EventSeverity.WARNING,
            summary=f"{event_type} happened",
            data=dict(data),
            created_by=None,
        )
        return event.id


async def _deliveries(admin_db: DatabaseSession, endpoint_id: str) -> list[WebhookDelivery]:
    async with admin_db.session() as session:
        return await WebhookDeliveryRepository.list_for_endpoint(session, endpoint_id)


# --- fan-out ------------------------------------------------------------------


async def test_fan_out_queues_one_delivery_per_subscriber(
    integration_context: Context, admin_db: DatabaseSession, clean_all: None
) -> None:
    """One event, three subscribers, three independent deliveries."""
    first = await _endpoint(admin_db, name="sub-a", event_types=[EventType.CREDENTIAL_EXPIRED])
    second = await _endpoint(admin_db, name="sub-b", event_types=[EventType.CREDENTIAL_EXPIRED])
    catch_all = await _endpoint(admin_db, name="sub-all", event_types=[])

    service = WebhookFanoutService(integration_context)
    async with admin_db.transaction() as session:
        ids = await service.fan_out(
            session,
            source_event_id="evt_fake_1",
            event_type=EventType.CREDENTIAL_EXPIRED,
            payload={"credential": "stripe-prod"},
        )

    assert len(ids) == 3
    for endpoint_id in (first, second, catch_all):
        assert len(await _deliveries(admin_db, endpoint_id)) == 1


async def test_fan_out_skips_unsubscribed_endpoints(
    integration_context: Context, admin_db: DatabaseSession, clean_all: None
) -> None:
    subscribed = await _endpoint(
        admin_db, name="wants-it", event_types=[EventType.CREDENTIAL_EXPIRED]
    )
    other = await _endpoint(admin_db, name="wants-other", event_types=[EventType.IMPORT_FAILED])

    service = WebhookFanoutService(integration_context)
    async with admin_db.transaction() as session:
        ids = await service.fan_out(
            session,
            source_event_id="evt_fake_2",
            event_type=EventType.CREDENTIAL_EXPIRED,
            payload={},
        )

    assert len(ids) == 1
    assert len(await _deliveries(admin_db, subscribed)) == 1
    assert await _deliveries(admin_db, other) == []


async def test_fan_out_skips_inactive_endpoints(
    integration_context: Context, admin_db: DatabaseSession, clean_all: None
) -> None:
    off = await _endpoint(admin_db, name="switched-off", active=False)

    service = WebhookFanoutService(integration_context)
    async with admin_db.transaction() as session:
        ids = await service.fan_out(
            session,
            source_event_id="evt_fake_3",
            event_type=EventType.CREDENTIAL_EXPIRED,
            payload={},
        )

    assert ids == []
    assert await _deliveries(admin_db, off) == []


async def test_fan_out_is_idempotent(
    integration_context: Context, admin_db: DatabaseSession, clean_all: None
) -> None:
    """Fanning out the same event twice must not double-deliver.

    This is what lets the relay be at-least-once safely.
    """
    endpoint_id = await _endpoint(admin_db, name="sub", event_types=[])
    service = WebhookFanoutService(integration_context)

    async with admin_db.transaction() as session:
        first = await service.fan_out(
            session,
            source_event_id="evt_same",
            event_type=EventType.CREDENTIAL_EXPIRED,
            payload={},
        )
    async with admin_db.transaction() as session:
        second = await service.fan_out(
            session,
            source_event_id="evt_same",
            event_type=EventType.CREDENTIAL_EXPIRED,
            payload={},
        )

    assert len(first) == 1
    assert second == [], "second fan-out must be suppressed by the dedupe constraint"
    assert len(await _deliveries(admin_db, endpoint_id)) == 1


async def test_withheld_event_types_are_never_relayed(
    integration_context: Context, admin_db: DatabaseSession, clean_all: None
) -> None:
    """A catch-all endpoint must not receive per-use credential audit records."""
    endpoint_id = await _endpoint(admin_db, name="catch-all", event_types=[])
    service = WebhookFanoutService(integration_context)

    async with admin_db.transaction() as session:
        ids = await service.fan_out(
            session,
            source_event_id="evt_sensitive",
            event_type=EventType.CREDENTIAL_ACCESSED,
            payload={},
        )

    assert ids == []
    assert await _deliveries(admin_db, endpoint_id) == []


async def test_fan_out_with_no_endpoints_is_not_an_error(
    integration_context: Context, admin_db: DatabaseSession, clean_all: None
) -> None:
    service = WebhookFanoutService(integration_context)
    async with admin_db.transaction() as session:
        assert (
            await service.fan_out(
                session,
                source_event_id="evt_nobody",
                event_type=EventType.CREDENTIAL_EXPIRED,
                payload={},
            )
            == []
        )


async def test_notification_payload_excludes_internal_detail() -> None:
    """``detail`` and actor columns must not leave the box."""
    payload = build_notification_payload(
        event_id="evt_1",
        event_type=EventType.CREDENTIAL_EXPIRED,
        severity=EventSeverity.WARNING,
        summary="a summary",
        data={"credential": "stripe-prod"},
        created_at="2026-08-13T00:00:00+00:00",
    )
    assert set(payload) == {"event_id", "event_type", "severity", "summary", "created_at", "data"}
    assert "detail" not in payload
    assert "actor_id" not in payload


async def test_notification_payload_forwards_enriched_display_data_but_drops_detail() -> None:
    """Phase 2 enrichment: pre-resolved DISPLAY fields ride through fan-out verbatim.

    ``build_notification_payload`` takes only the already-shaped ``data`` an emit
    site produced, so the anti-exfiltration boundary is structural: there is no
    parameter through which ``detail`` or an actor column could reach the wire.
    This pins both halves — the enriched display fields survive, and no forbidden
    field appears — so a future regression that widens the payload trips a test.
    """
    enriched = {
        "execution_id": "exec_123",
        "operation_id": "chargesCreate",
        "api": {"vendor": "stripe", "name": "api", "version": "2020-08-27"},
        "duration_ms": 142,
        "http_status": 200,
    }
    payload = build_notification_payload(
        event_id="evt_2",
        event_type=EventType.EXECUTION_COMPLETED,
        severity=EventSeverity.INFO,
        summary="Execution of chargesCreate completed in 142ms",
        data=enriched,
        created_at="2026-08-13T00:00:00+00:00",
    )
    # Enriched display data is forwarded unchanged.
    assert payload["data"] == enriched
    assert payload["summary"] == "Execution of chargesCreate completed in 142ms"
    # Anti-exfil: no dropped/forbidden field can appear at the top level or in data.
    for forbidden in ("detail", "actor_id", "actor_type", "created_by", "requires_action"):
        assert forbidden not in payload
    for forbidden in ("secret", "credential", "token", "body", "response", "detail"):
        assert forbidden not in payload["data"]


# --- the relay ----------------------------------------------------------------


async def test_relay_turns_a_real_event_into_a_delivery(
    integration_context: Context, admin_db: DatabaseSession, clean_all: None
) -> None:
    """The headline behaviour: platform emits, subscriber gets queued a delivery."""
    endpoint_id = await _endpoint(admin_db, name="ops", event_types=[EventType.CREDENTIAL_EXPIRED])
    relay = InternalEventRelay(integration_context, relay_lag=0.0)
    # Establish the cursor before emitting, as a long-running process would.
    await relay.relay_once()

    event_id = await _emit(admin_db, EventType.CREDENTIAL_EXPIRED, credential="stripe-prod")
    handled = await relay.relay_once()

    assert handled == 1
    rows = await _deliveries(admin_db, endpoint_id)
    assert len(rows) == 1

    async with admin_db.session() as session:
        webhook_event = await WebhookEventRepository.get_by_id(session, rows[0].event_id)
    assert webhook_event is not None
    assert webhook_event.source_event_id == event_id, "delivery must trace to the real event"
    assert webhook_event.payload["event_type"] == EventType.CREDENTIAL_EXPIRED
    assert webhook_event.payload["data"]["credential"] == "stripe-prod"


async def test_relay_starts_from_now_on_a_fresh_install(
    integration_context: Context, admin_db: DatabaseSession, clean_all: None
) -> None:
    """Historical events must not be dumped at a newly created endpoint."""
    await _emit(admin_db, EventType.CREDENTIAL_EXPIRED, note="ancient history")
    endpoint_id = await _endpoint(admin_db, name="new-endpoint", event_types=[])

    relay = InternalEventRelay(integration_context, relay_lag=0.0)
    await relay.relay_once()

    assert await _deliveries(admin_db, endpoint_id) == []


async def test_relay_does_not_replay_already_relayed_events(
    integration_context: Context, admin_db: DatabaseSession, clean_all: None
) -> None:
    endpoint_id = await _endpoint(admin_db, name="ops", event_types=[])
    relay = InternalEventRelay(integration_context, relay_lag=0.0)
    await relay.relay_once()

    await _emit(admin_db, EventType.CREDENTIAL_EXPIRED)
    assert await relay.relay_once() == 1
    assert await relay.relay_once() == 0, "cursor must have advanced past the event"
    assert len(await _deliveries(admin_db, endpoint_id)) == 1


async def test_relay_advances_through_a_batch(
    integration_context: Context, admin_db: DatabaseSession, clean_all: None
) -> None:
    endpoint_id = await _endpoint(admin_db, name="ops", event_types=[])
    relay = InternalEventRelay(integration_context, batch_limit=2, relay_lag=0.0)
    await relay.relay_once()

    for _ in range(5):
        await _emit(admin_db, EventType.CREDENTIAL_EXPIRED)

    total = 0
    for _ in range(5):
        total += await relay.relay_once()
    assert total == 5
    assert len(await _deliveries(admin_db, endpoint_id)) == 5


async def test_relay_resumes_after_a_restart(
    integration_context: Context, admin_db: DatabaseSession, clean_all: None
) -> None:
    """A new relay instance must not re-deliver, nor skip what it missed.

    The cursor is derived from what has already been relayed, so a fresh
    instance (a restarted process) picks up where the old one left off.
    """
    endpoint_id = await _endpoint(admin_db, name="ops", event_types=[])

    first_relay = InternalEventRelay(integration_context, relay_lag=0.0)
    await first_relay.relay_once()
    await _emit(admin_db, EventType.CREDENTIAL_EXPIRED, seq=1)
    await first_relay.relay_once()
    assert len(await _deliveries(admin_db, endpoint_id)) == 1

    # Process restarts: brand-new instance, no in-memory cursor.
    await _emit(admin_db, EventType.CREDENTIAL_EXPIRED, seq=2)
    second_relay = InternalEventRelay(integration_context, relay_lag=0.0)
    handled = await second_relay.relay_once()

    assert handled == 1, "must pick up the event emitted while 'down'"
    assert len(await _deliveries(admin_db, endpoint_id)) == 2, "and not re-deliver the first"


async def test_relay_with_no_subscribers_still_advances(
    integration_context: Context, admin_db: DatabaseSession, clean_all: None
) -> None:
    """Events must not pile up as perpetually-unread when nobody subscribes."""
    relay = InternalEventRelay(integration_context, relay_lag=0.0)
    await relay.relay_once()

    await _emit(admin_db, EventType.CREDENTIAL_EXPIRED)
    assert await relay.relay_once() == 1
    assert await relay.relay_once() == 0


async def test_relay_cursor_survives_events_in_the_same_moment(
    integration_context: Context, admin_db: DatabaseSession, clean_all: None
) -> None:
    """Two events sharing a timestamp must both be relayed exactly once.

    A timestamp-only cursor would skip one of them.
    """
    endpoint_id = await _endpoint(admin_db, name="ops", event_types=[])
    relay = InternalEventRelay(integration_context, relay_lag=0.0)
    await relay.relay_once()

    same_moment = datetime.now(UTC)
    async with admin_db.transaction() as session:
        for index in range(3):
            event = await EventRepository.create(
                session,
                type=EventType.CREDENTIAL_EXPIRED,
                severity=EventSeverity.WARNING,
                summary=f"same-moment {index}",
                created_by=None,
            )
            event.created_at = same_moment

    total = 0
    for _ in range(4):
        total += await relay.relay_once()

    assert total == 3
    assert len(await _deliveries(admin_db, endpoint_id)) == 3


# --- relay lag / commit-order safety (item 2) --------------------------------


async def test_relay_lag_skips_events_inside_the_window(
    integration_context: Context, admin_db: DatabaseSession, clean_all: None
) -> None:
    """With a positive lag, an event newer than ``now - lag`` is not yet relayed.

    This is the commit-order safety margin: a very fresh event might belong to a
    transaction that has not finished committing, so the relay holds off until it
    ages past the lag window.
    """
    endpoint_id = await _endpoint(admin_db, name="ops", event_types=[])
    relay = InternalEventRelay(integration_context, relay_lag=3600.0)
    await relay.relay_once()

    # A brand-new event is well inside a 1-hour lag window.
    await _emit(admin_db, EventType.CREDENTIAL_EXPIRED)
    assert await relay.relay_once() == 0, "an event inside the lag window must be withheld"
    assert await _deliveries(admin_db, endpoint_id) == []


async def test_relay_lag_relays_events_older_than_the_window(
    integration_context: Context, admin_db: DatabaseSession, clean_all: None
) -> None:
    """An event older than ``now - lag`` is past the safety margin and relays."""
    endpoint_id = await _endpoint(admin_db, name="ops", event_types=[])

    # An event that sits after the cursor but comfortably behind the 5s window.
    async with admin_db.transaction() as session:
        event = await EventRepository.create(
            session,
            type=EventType.CREDENTIAL_EXPIRED,
            severity=EventSeverity.WARNING,
            summary="aged event",
            created_by=None,
        )
        event.created_at = datetime.now(UTC) - timedelta(seconds=60)

    relay = InternalEventRelay(integration_context, relay_lag=5.0)
    # Pin the resume point before the event (a real relay derives this from the
    # last relayed event; here we set it explicitly so the aged event is in
    # range rather than being treated as pre-install history).
    relay._cursor = (datetime.now(UTC) - timedelta(seconds=300), "")

    assert await relay.relay_once() == 1
    assert len(await _deliveries(admin_db, endpoint_id)) == 1


# --- subscriber lookup (item 8) ----------------------------------------------


async def test_list_subscribers_matches_type_and_catch_all(
    integration_context: Context, admin_db: DatabaseSession, clean_all: None
) -> None:
    """The subscriber query returns type-matched + catch-all, skips others/inactive.

    On Postgres this exercises the JSONB-containment branch; on SQLite the
    portable Python-filter branch. Both must agree on the result set.
    """
    wants = await _endpoint(admin_db, name="wants", event_types=[EventType.CREDENTIAL_EXPIRED])
    catch_all = await _endpoint(admin_db, name="all", event_types=[])
    other = await _endpoint(admin_db, name="other", event_types=[EventType.IMPORT_FAILED])
    off = await _endpoint(
        admin_db, name="off", event_types=[EventType.CREDENTIAL_EXPIRED], active=False
    )

    async with admin_db.session() as session:
        subs = await WebhookEndpointRepository.list_subscribers(
            session, EventType.CREDENTIAL_EXPIRED
        )
    ids = {s.id for s in subs}
    assert wants in ids
    assert catch_all in ids
    assert other not in ids, "a type-mismatched endpoint must not match"
    assert off not in ids, "an inactive endpoint must not match"


# --- delivered-payload enrichment (async worker path) ------------------------


class _OkExecutor(UpstreamExecutor):
    """Executor stub returning a fixed 200 with a measured duration.

    Stands in for the broker's ``PipelineExecutor`` so the async worker path can
    be driven without a real upstream call — the handler is what builds the
    lifecycle event, which is what this test pins.
    """

    async def execute(self, request: UpstreamExecRequest, *, session: Any) -> UpstreamExecResult:
        return UpstreamExecResult(
            status_code=200, body=b"ok", content_type="application/json", duration_ms=42
        )


async def test_async_worker_execution_delivers_enriched_payload(
    integration_context: Context,
    admin_db: DatabaseSession,
    clean_all: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The async worker's ``execution.completed`` must reach the wire ENRICHED.

    Regression for the Phase-2 gap: the async ``ExecutionHandler`` emit site was
    left with an id-based summary and empty ``data`` while only the sync/streaming
    broker path was enriched, so an enqueued (202) execution delivered
    ``{"summary": "Execution completed (job …)", "data": {}}`` — the ugly Slack
    output. This drives the REAL worker → relay fan-out and asserts the payload
    the receiver would get (the persisted ``webhook_events.payload``) carries the
    human summary + resolved display fields, not merely that a helper returns them.
    """
    endpoint_id = await _endpoint(admin_db, name="ops", event_types=[EventType.EXECUTION_COMPLETED])
    relay = InternalEventRelay(integration_context, relay_lag=0.0)
    await relay.relay_once()  # establish the cursor before emitting

    # SSRF validation is orthogonal here — the executor is a stub, so keep the
    # url as-is rather than resolving DNS in this test.
    monkeypatch.setattr(
        "jentic_one.shared.jobs.execution_handler.validate_upstream_url",
        lambda url, egress: url,
    )

    handler = ExecutionHandler(executor=_OkExecutor())
    async with admin_db.transaction() as session:
        await handler.execute(
            "job_06e08d4c9a1b2c3d4e5f6a7b",
            session=session,
            payload={
                "upstream_url": "https://api.stripe.com/v1/charges",
                "method": "POST",
                "execution_id": "exec_06e08d4c9a1b2c3d4e5f6a7b",
                "toolkit_id": "tk_stripe0000000000000000",
                "operation_id": "createCharge",
                "api_vendor": "stripe",
                "api_name": "api",
                "api_version": "2024-06-20",
                "trace_id": "a" * 32,
            },
            created_by="agt_abc",
            actor_type="agent",
        )

    handled = await relay.relay_once()
    assert handled == 1, "the worker's execution.completed must be relayed"

    rows = await _deliveries(admin_db, endpoint_id)
    assert len(rows) == 1
    async with admin_db.session() as session:
        webhook_event = await WebhookEventRepository.get_by_id(session, rows[0].event_id)
    assert webhook_event is not None
    payload = webhook_event.payload

    # The human summary names WHAT ran + how long — never the bare id/job form.
    assert payload["summary"] == "Execution of createCharge completed in 42ms"
    assert "(job " not in payload["summary"], "must not be the id-based worker summary"

    # The curated display fields the Slack relay reads are present on the wire.
    data = payload["data"]
    assert data["operation_id"] == "createCharge"
    assert data["toolkit_id"] == "tk_stripe0000000000000000"
    assert data["api"] == {"vendor": "stripe", "name": "api", "version": "2024-06-20"}
    assert data["duration_ms"] == 42
    assert data["http_status"] == 200

    # Anti-exfil: no secrets, credential material, raw body, or dropped columns.
    for forbidden in ("detail", "actor_id", "actor_type", "created_by"):
        assert forbidden not in payload
    assert not any(
        k in data for k in ("secret", "credential", "token", "body", "response", "detail")
    )


# --- resend resets the attempt budget (item 12) ------------------------------


async def test_resend_resets_attempt_count(
    integration_context: Context, admin_db: DatabaseSession, clean_all: None
) -> None:
    """A manual resend must restore the full retry budget, not leave it exhausted."""
    endpoint_id = await _endpoint(admin_db, name="ops", event_types=[])
    async with admin_db.transaction() as session:
        event = await WebhookEventRepository.record_event(
            session,
            endpoint_id=endpoint_id,
            source_event_id="resend-src",
            event_type=EventType.CREDENTIAL_EXPIRED,
            payload={},
        )
        assert event is not None
        delivery = await WebhookDeliveryRepository.enqueue(
            session, event_id=event.id, endpoint_id=endpoint_id
        )
        delivery_id = delivery.id
        # Simulate an exhausted dead-letter: several recorded failures.
        await WebhookDeliveryRepository.mark_failed(
            session, delivery_id, status_code=500, error="http_error_500", retry_in=None
        )
        await WebhookDeliveryRepository.mark_failed(
            session, delivery_id, status_code=500, error="http_error_500", retry_in=None
        )

    async with admin_db.session() as session:
        rows = await WebhookDeliveryRepository.list_for_endpoint(session, endpoint_id)
        assert rows[0].attempt_count == 2

    async with admin_db.transaction() as session:
        assert await WebhookDeliveryRepository.reset_for_resend(session, delivery_id) is True

    async with admin_db.session() as session:
        rows = await WebhookDeliveryRepository.list_for_endpoint(session, endpoint_id)
    assert rows[0].attempt_count == 0, "resend must reset the attempt budget"
    assert rows[0].status == "pending"
    assert rows[0].last_error is None
