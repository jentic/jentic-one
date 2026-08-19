"""Integration tests for the outbound webhook delivery dispatcher.

The database is real (per the no-DB-mocking rule); only the outbound HTTP
transport is substituted, via ``httpx.MockTransport``, so receiver behaviour —
200, 500, 410, timeout — can be driven deterministically.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import pytest_asyncio

from jentic_one.admin.repos.webhook_repo import (
    STATUS_DEAD,
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    WebhookDeliveryRepository,
    WebhookEndpointRepository,
    WebhookEventRepository,
)
from jentic_one.shared.webhooks.delivery import WebhookDeliveryDispatcher
from jentic_one.shared.webhooks.signing import (
    HEADER_ID,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    SCHEME,
    compute_signature,
)

SECRET = "whsec_dispatcher_test"  # pragma: allowlist secret

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def queued(admin_db: Any) -> AsyncIterator[dict[str, str]]:
    """One notification endpoint + event + queued delivery; cleaned up after."""
    async with admin_db.transaction() as session:
        endpoint = await WebhookEndpointRepository.create(
            session,
            name="dispatcher-test-listener",
            secret_hash="unused-here",  # pragma: allowlist secret
            secret_encrypted="unused-here",  # pragma: allowlist secret
            target_url="https://receiver.test/hook",
            event_types=[],
            created_by="test",
        )
        event = await WebhookEventRepository.record_event(
            session,
            endpoint_id=endpoint.id,
            source_event_id="src-1",
            event_type="credential.expired",
            payload={"credential": "stripe-prod"},
        )
        assert event is not None
        delivery = await WebhookDeliveryRepository.enqueue(
            session, event_id=event.id, endpoint_id=endpoint.id
        )
        ids = {
            "endpoint_id": endpoint.id,
            "event_id": event.id,
            "delivery_id": delivery.id,
        }

    yield ids

    # Cascades reap the event and delivery rows.
    async with admin_db.transaction() as session:
        await WebhookEndpointRepository.delete(session, ids["endpoint_id"])


def _dispatcher(
    admin_db: Any,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    secret: str | None = SECRET,
    **kwargs: Any,
) -> WebhookDeliveryDispatcher:
    return WebhookDeliveryDispatcher(
        admin_db,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        secret_resolver=lambda _endpoint: secret,
        **kwargs,
    )


async def _row(admin_db: Any, endpoint_id: str) -> Any:
    """Re-read the (single) delivery row for an endpoint."""
    async with admin_db.transaction() as session:
        rows = await WebhookDeliveryRepository.list_for_endpoint(session, endpoint_id)
        return rows[0]


async def _make_due_again(admin_db: Any, delivery_id: str) -> None:
    """Skip the backoff wait instead of sleeping through it."""
    async with admin_db.transaction() as session:
        await WebhookDeliveryRepository.reset_for_resend(session, delivery_id)


# --- successful delivery ------------------------------------------------------


async def test_successful_delivery_is_marked_succeeded(
    admin_db: Any, queued: dict[str, str]
) -> None:
    attempted = await _dispatcher(admin_db, lambda _req: httpx.Response(200)).drain_once()
    assert attempted == 1

    row = await _row(admin_db, queued["endpoint_id"])
    assert row.status == STATUS_SUCCEEDED
    assert row.last_status_code == 200
    assert row.next_attempt_at is None


async def test_outbound_request_is_verifiably_signed(admin_db: Any, queued: dict[str, str]) -> None:
    """A receiver must be able to verify us using the standard scheme."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content
        return httpx.Response(200)

    await _dispatcher(admin_db, handler).drain_once()

    assert {HEADER_ID, HEADER_TIMESTAMP, HEADER_SIGNATURE} <= set(captured["headers"])
    # Recompute over the exact transmitted bytes — the whole point.
    headers = captured["headers"]
    expected = compute_signature(
        SECRET, headers[HEADER_ID], headers[HEADER_TIMESTAMP], captured["body"]
    )
    assert headers[HEADER_SIGNATURE] == f"{SCHEME},{expected}"


async def test_signed_id_is_the_event_id(admin_db: Any, queued: dict[str, str]) -> None:
    """Receivers dedupe on webhook-id, so it must be the stable event id."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["id"] = request.headers[HEADER_ID]
        return httpx.Response(200)

    await _dispatcher(admin_db, handler).drain_once()
    assert captured["id"] == queued["event_id"]


async def test_body_is_json_with_id_type_and_data(admin_db: Any, queued: dict[str, str]) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = request.read()
        return httpx.Response(200)

    await _dispatcher(admin_db, handler).drain_once()

    body = json.loads(captured["json"])
    assert body["id"] == queued["event_id"]
    assert body["type"] == "credential.expired"
    assert body["data"] == {"credential": "stripe-prod"}


async def test_empty_queue_sends_nothing(admin_db: Any) -> None:
    called = False

    def handler(_req: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    assert await _dispatcher(admin_db, handler).drain_once() == 0
    assert called is False


# --- retry and dead-letter ----------------------------------------------------


async def test_server_error_schedules_a_retry(admin_db: Any, queued: dict[str, str]) -> None:
    await _dispatcher(admin_db, lambda _req: httpx.Response(500)).drain_once()

    row = await _row(admin_db, queued["endpoint_id"])
    assert row.status == STATUS_FAILED
    assert row.last_status_code == 500
    assert row.attempt_count == 1
    assert row.next_attempt_at is not None
    assert row.next_attempt_at > datetime.now(UTC)


async def test_network_failure_schedules_a_retry(admin_db: Any, queued: dict[str, str]) -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connection timed out")

    await _dispatcher(admin_db, handler).drain_once()

    row = await _row(admin_db, queued["endpoint_id"])
    assert row.status == STATUS_FAILED
    assert row.last_status_code is None
    assert "ConnectTimeout" in row.last_error


async def test_backoff_prevents_immediate_reclaim(admin_db: Any, queued: dict[str, str]) -> None:
    dispatcher = _dispatcher(admin_db, lambda _req: httpx.Response(500))
    await dispatcher.drain_once()
    # The retry is in the future, so a second drain must find nothing.
    assert await dispatcher.drain_once() == 0


async def test_exhausted_attempts_are_dead_lettered(admin_db: Any, queued: dict[str, str]) -> None:
    """After max_attempts the row parks as dead rather than retrying forever."""
    dispatcher = _dispatcher(admin_db, lambda _req: httpx.Response(503), max_attempts=2)

    await dispatcher.drain_once()
    assert (await _row(admin_db, queued["endpoint_id"])).status == STATUS_FAILED

    await _make_due_again(admin_db, queued["delivery_id"])
    await dispatcher.drain_once()

    row = await _row(admin_db, queued["endpoint_id"])
    assert row.status == STATUS_DEAD
    assert row.next_attempt_at is None


async def test_successful_retry_clears_the_error(admin_db: Any, queued: dict[str, str]) -> None:
    responses = [httpx.Response(500), httpx.Response(200)]

    dispatcher = _dispatcher(admin_db, lambda _req: responses.pop(0))
    await dispatcher.drain_once()
    assert (await _row(admin_db, queued["endpoint_id"])).status == STATUS_FAILED

    await _make_due_again(admin_db, queued["delivery_id"])
    await dispatcher.drain_once()

    row = await _row(admin_db, queued["endpoint_id"])
    assert row.status == STATUS_SUCCEEDED
    assert row.last_error is None


# --- receiver asks us to stop -------------------------------------------------


async def test_410_deactivates_the_endpoint(admin_db: Any, queued: dict[str, str]) -> None:
    """410 Gone means "stop sending" — continuing to retry would be abuse."""
    await _dispatcher(admin_db, lambda _req: httpx.Response(410)).drain_once()

    assert (await _row(admin_db, queued["endpoint_id"])).status == STATUS_DEAD

    async with admin_db.transaction() as session:
        endpoint = await WebhookEndpointRepository.get_by_id(session, queued["endpoint_id"])
        assert endpoint is not None
        assert endpoint.active is False


# --- refuse to send unsigned --------------------------------------------------


async def test_delivery_without_a_secret_is_not_sent(admin_db: Any, queued: dict[str, str]) -> None:
    """Sending unsigned is never an acceptable fallback."""
    sent = False

    def handler(_req: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent = True
        return httpx.Response(200)

    await _dispatcher(admin_db, handler, secret=None).drain_once()

    assert sent is False, "must not send when no signing secret is available"
    assert (await _row(admin_db, queued["endpoint_id"])).status == STATUS_DEAD
