"""Integration tests for the outbound webhook delivery dispatcher.

The database is real (per the no-DB-mocking rule); only the outbound HTTP
transport is substituted, via ``httpx.MockTransport``, so receiver behaviour —
200, 500, 410, timeout — can be driven deterministically.
"""

from __future__ import annotations

import json
import socket
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio

from jentic_one.admin.core.schema.webhook_deliveries import WebhookDelivery
from jentic_one.admin.repos.webhook_repo import (
    STATUS_DEAD,
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    WebhookDeliveryRepository,
    WebhookEndpointRepository,
    WebhookEventRepository,
)
from jentic_one.shared.egress import DnsPinningTransport
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
    """Make a failed row due again *without* touching its attempt budget.

    Deliberately not ``reset_for_resend`` — that is the manual-resend path which
    (correctly, item 12) resets ``attempt_count`` too. Here we only want to skip
    the backoff wait so the natural retry runs on the next drain, preserving the
    accumulated attempt count.
    """
    async with admin_db.transaction() as session:
        row = await session.get(WebhookDelivery, delivery_id)
        assert row is not None
        row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.flush()


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
    # last_error is a *categorized* reason now (item 4), never the raw exception
    # string — so the pinned internal IP / target URL can't leak through the API.
    assert row.last_error == "connection_timeout"


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


# --- categorized last_error (item 4) -----------------------------------------


async def test_last_error_is_categorized_not_raw(admin_db: Any, queued: dict[str, str]) -> None:
    """A raw exception can embed the pinned internal IP / URL; we store a category.

    The handler raises a ConnectError whose message contains an internal-looking
    address, and we assert that address never lands in the persisted last_error.
    """

    def handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("failed to connect to 169.254.169.254:80")

    await _dispatcher(admin_db, handler).drain_once()

    row = await _row(admin_db, queued["endpoint_id"])
    assert row.last_error == "connect_failed"
    assert "169.254" not in (row.last_error or "")


async def test_http_error_is_categorized_by_status(admin_db: Any, queued: dict[str, str]) -> None:
    await _dispatcher(admin_db, lambda _req: httpx.Response(500)).drain_once()
    row = await _row(admin_db, queued["endpoint_id"])
    assert row.last_error == "http_error_500"


# --- response-size cap (item 5) ----------------------------------------------


async def test_oversized_2xx_body_is_a_failure_not_a_success(
    admin_db: Any, queued: dict[str, str]
) -> None:
    """A hostile 200 with a huge body must not be buffered or counted a success.

    The dispatcher streams the response and abandons it once the cap is crossed;
    an oversized body on an otherwise-2xx response is treated as a delivery
    failure (the receiver is misbehaving), so the row is scheduled for retry
    rather than marked succeeded.
    """
    # Comfortably over the 8 KiB cap.
    huge = b"x" * (64 * 1024)

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=huge)

    await _dispatcher(admin_db, handler).drain_once()

    row = await _row(admin_db, queued["endpoint_id"])
    assert row.status == STATUS_FAILED
    assert row.last_error == "response_too_large"


async def test_small_2xx_body_still_succeeds(admin_db: Any, queued: dict[str, str]) -> None:
    """The cap must not reject a normal, small response body."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"ok":true}')

    await _dispatcher(admin_db, handler).drain_once()
    assert (await _row(admin_db, queued["endpoint_id"])).status == STATUS_SUCCEEDED


# --- delivery lease / attempt semantics (item 3) -----------------------------


async def test_claim_leases_row_and_does_not_bump_attempt(
    admin_db: Any, queued: dict[str, str]
) -> None:
    """Claiming moves a row to ``sending`` and pushes visibility, without burning
    an attempt — the attempt is only counted at a real outcome."""
    async with admin_db.transaction() as session:
        claimed = await WebhookDeliveryRepository.claim_due(session, limit=10)
        assert len(claimed) == 1
        # attempt_count is untouched at claim time.
        assert claimed[0].attempt_count == 0
        assert claimed[0].status == "sending"

    # A second claim before the lease lapses must find nothing (no double-send).
    async with admin_db.transaction() as session:
        again = await WebhookDeliveryRepository.claim_due(session, limit=10)
        assert again == []


async def test_lapsed_lease_is_reclaimable(admin_db: Any, queued: dict[str, str]) -> None:
    """A ``sending`` row whose lease has lapsed becomes claimable again."""
    async with admin_db.transaction() as session:
        # A tiny lease so it is already expired for the reclaim check.
        first = await WebhookDeliveryRepository.claim_due(session, limit=10, lease_s=0.0)
        assert len(first) == 1

    async with admin_db.transaction() as session:
        # now slightly in the future so the 0s lease has lapsed.
        reclaimed = await WebhookDeliveryRepository.claim_due(
            session, limit=10, now=datetime.now(UTC) + timedelta(seconds=1)
        )
        assert len(reclaimed) == 1
        # Still no attempt burned by the crash-and-reclaim.
        assert reclaimed[0].attempt_count == 0


# --- per-endpoint concurrency cap (item 11) ----------------------------------


async def test_per_endpoint_cap_limits_in_flight_claims(admin_db: Any) -> None:
    """With cap=1, a single claim batch takes at most one row per endpoint."""
    async with admin_db.transaction() as session:
        endpoint = await WebhookEndpointRepository.create(
            session,
            name="cap-test-listener",
            secret_hash="unused",  # pragma: allowlist secret
            secret_encrypted="unused",  # pragma: allowlist secret
            target_url="https://receiver.test/hook",
            event_types=[],
            created_by="test",
        )
        endpoint_id = endpoint.id
        for i in range(3):
            event = await WebhookEventRepository.record_event(
                session,
                endpoint_id=endpoint_id,
                source_event_id=f"cap-src-{i}",
                event_type="credential.expired",
                payload={},
            )
            assert event is not None
            await WebhookDeliveryRepository.enqueue(
                session, event_id=event.id, endpoint_id=endpoint_id
            )

    try:
        async with admin_db.transaction() as session:
            claimed = await WebhookDeliveryRepository.claim_due(
                session, limit=10, max_in_flight_per_endpoint=1
            )
            assert len(claimed) == 1, "cap=1 must claim at most one row for the endpoint"

        async with admin_db.transaction() as session:
            remaining = await WebhookDeliveryRepository.claim_due(
                session, limit=10, max_in_flight_per_endpoint=0, now=datetime.now(UTC)
            )
            # The first (capped) claim leased exactly one of the three rows; the
            # other two are still pending and due, so an *uncapped* claim now
            # takes both — proving the cap, not the queue, is what limited the
            # first batch to one.
            assert len(remaining) == 2
    finally:
        async with admin_db.transaction() as session:
            await WebhookEndpointRepository.delete(session, endpoint_id)


# --- delivery-log retention / pruning (item 10) ------------------------------


async def test_prune_deletes_old_succeeded_keeps_dead(admin_db: Any) -> None:
    """Pruning reaps aged ``succeeded`` rows but never a ``dead`` dead-letter."""
    async with admin_db.transaction() as session:
        endpoint = await WebhookEndpointRepository.create(
            session,
            name="prune-test-listener",
            secret_hash="unused",  # pragma: allowlist secret
            secret_encrypted="unused",  # pragma: allowlist secret
            target_url="https://receiver.test/hook",
            event_types=[],
            created_by="test",
        )
        endpoint_id = endpoint.id
        succeeded_event = await WebhookEventRepository.record_event(
            session,
            endpoint_id=endpoint_id,
            source_event_id="prune-ok",
            event_type="credential.expired",
            payload={},
        )
        dead_event = await WebhookEventRepository.record_event(
            session,
            endpoint_id=endpoint_id,
            source_event_id="prune-dead",
            event_type="credential.expired",
            payload={},
        )
        assert succeeded_event is not None and dead_event is not None
        ok_delivery = await WebhookDeliveryRepository.enqueue(
            session, event_id=succeeded_event.id, endpoint_id=endpoint_id
        )
        dead_delivery = await WebhookDeliveryRepository.enqueue(
            session, event_id=dead_event.id, endpoint_id=endpoint_id
        )
        await WebhookDeliveryRepository.mark_succeeded(session, ok_delivery.id, status_code=200)
        await WebhookDeliveryRepository.mark_failed(
            session, dead_delivery.id, status_code=None, error="x", retry_in=None
        )
        # Age the succeeded row's last_attempt_at well into the past.
        succeeded_row = await session.get(WebhookDelivery, ok_delivery.id)
        assert succeeded_row is not None
        succeeded_row.last_attempt_at = datetime.now(UTC) - timedelta(days=90)
        await session.flush()

    try:
        async with admin_db.transaction() as session:
            deleted = await WebhookDeliveryRepository.prune_succeeded(
                session, older_than=datetime.now(UTC) - timedelta(days=30)
            )
        assert deleted == 1

        async with admin_db.transaction() as session:
            remaining = await WebhookDeliveryRepository.list_for_endpoint(session, endpoint_id)
        statuses = {r.status for r in remaining}
        assert statuses == {STATUS_DEAD}, "only the dead-letter should survive"
    finally:
        async with admin_db.transaction() as session:
            await WebhookEndpointRepository.delete(session, endpoint_id)


# --- response-time (duration_ms) + per-attempt history (Phase 3) -------------


async def test_success_records_duration_and_one_attempt_row(
    admin_db: Any, queued: dict[str, str]
) -> None:
    """A successful send stamps ``duration_ms`` and appends exactly one history row."""
    await _dispatcher(admin_db, lambda _req: httpx.Response(200)).drain_once()

    row = await _row(admin_db, queued["endpoint_id"])
    assert row.status == STATUS_SUCCEEDED
    # duration_ms is a wall-clock measurement — assert it exists and is sane
    # rather than pinning a value.
    assert row.duration_ms is not None
    assert row.duration_ms >= 0

    async with admin_db.transaction() as session:
        attempts = await WebhookDeliveryRepository.list_attempts(session, queued["delivery_id"])
    assert len(attempts) == 1
    assert attempts[0].attempt_number == 1
    assert attempts[0].status_code == 200
    assert attempts[0].error is None
    assert attempts[0].duration_ms is not None


async def test_retry_then_success_records_two_attempt_rows_in_order(
    admin_db: Any, queued: dict[str, str]
) -> None:
    """Each real attempt appends its own ordinal-numbered history row."""
    responses = [httpx.Response(500), httpx.Response(200)]
    dispatcher = _dispatcher(admin_db, lambda _req: responses.pop(0))

    await dispatcher.drain_once()
    await _make_due_again(admin_db, queued["delivery_id"])
    await dispatcher.drain_once()

    async with admin_db.transaction() as session:
        attempts = await WebhookDeliveryRepository.list_attempts(session, queued["delivery_id"])

    # Newest-first per the repo ordering.
    assert [a.attempt_number for a in attempts] == [2, 1]
    by_number = {a.attempt_number: a for a in attempts}
    assert by_number[1].status_code == 500
    assert by_number[1].error == "http_error_500"
    assert by_number[2].status_code == 200
    assert by_number[2].error is None


async def test_network_failure_records_attempt_with_no_status(
    admin_db: Any, queued: dict[str, str]
) -> None:
    """A transport failure still records a history row (no status, safe category)."""

    def handler(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    await _dispatcher(admin_db, handler).drain_once()

    async with admin_db.transaction() as session:
        attempts = await WebhookDeliveryRepository.list_attempts(session, queued["delivery_id"])
    assert len(attempts) == 1
    assert attempts[0].status_code is None
    assert attempts[0].error == "connect_failed"


# --- aggregate stats (Phase 3, drawer Overview) ------------------------------


async def test_aggregate_reflects_a_succeeded_delivery(
    admin_db: Any, queued: dict[str, str]
) -> None:
    """The Overview aggregate summarises the delivery's real outcome + duration."""
    await _dispatcher(admin_db, lambda _req: httpx.Response(200)).drain_once()

    async with admin_db.transaction() as session:
        stats = await WebhookDeliveryRepository.aggregate_for_endpoint(
            session, queued["endpoint_id"]
        )

    assert stats["total"] == 1
    assert stats["counts_by_status"].get(STATUS_SUCCEEDED) == 1
    assert stats["recent_total"] == 1
    assert stats["recent_failed"] == 0
    assert stats["last_status_code"] == 200
    assert stats["last_attempt_at"] is not None
    assert stats["avg_duration_ms"] is not None


async def test_aggregate_counts_a_dead_letter_as_recent_failure(
    admin_db: Any, queued: dict[str, str]
) -> None:
    dispatcher = _dispatcher(admin_db, lambda _req: httpx.Response(503), max_attempts=1)
    await dispatcher.drain_once()

    async with admin_db.transaction() as session:
        stats = await WebhookDeliveryRepository.aggregate_for_endpoint(
            session, queued["endpoint_id"]
        )

    assert stats["counts_by_status"].get(STATUS_DEAD) == 1
    assert stats["recent_failed"] == 1
    assert stats["last_status_code"] == 503


async def test_aggregate_avg_duration_averages_every_attempt_not_just_last(
    admin_db: Any, queued: dict[str, str]
) -> None:
    """M2: ``avg_duration_ms`` is the mean over ALL attempts, not the last one.

    The parent ``webhook_deliveries.duration_ms`` is overwritten on each attempt
    and so holds only the most recent attempt's duration; averaging *that* would
    misrepresent latency whenever a delivery retried. This records two attempts
    for one delivery (100ms then 300ms) via the per-attempt history and asserts
    the aggregate reports their true mean (200ms) — not the last value (300ms).
    """
    async with admin_db.transaction() as session:
        # Attempt 1: a failure at 100ms (parent duration_ms becomes 100).
        await WebhookDeliveryRepository.mark_failed(
            session,
            queued["delivery_id"],
            status_code=500,
            error="http_error_500",
            retry_in=timedelta(seconds=1),
            duration_ms=100,
            attempt_number=1,
        )
        # Attempt 2: a success at 300ms (parent duration_ms is overwritten to 300).
        await WebhookDeliveryRepository.mark_succeeded(
            session,
            queued["delivery_id"],
            status_code=200,
            duration_ms=300,
            attempt_number=2,
        )

    async with admin_db.transaction() as session:
        stats = await WebhookDeliveryRepository.aggregate_for_endpoint(
            session, queued["endpoint_id"]
        )
        # Guard the premise: the parent only kept the *last* attempt's duration.
        row = await WebhookDeliveryRepository.list_for_endpoint(session, queued["endpoint_id"])
        assert row[0].duration_ms == 300

    # The per-attempt mean of {100, 300} is 200 — averaging the parent's single
    # 300 value (the old behaviour) would wrongly report 300.
    assert stats["avg_duration_ms"] == 200.0


async def test_cross_dispatcher_cap_counts_existing_live_leases(admin_db: Any) -> None:
    """M1: the per-endpoint cap holds ACROSS concurrent claims, not just within one.

    Two due rows for one endpoint. A first ``claim_due(cap=1)`` leases one and
    moves it to ``sending`` with a live (future) lease. A second, independent
    ``claim_due(cap=1)`` — a stand-in for a *second dispatcher* claiming while
    the first send is still in flight, using the same ``now`` so the first lease
    is still live — must lease **zero**: the endpoint already has one delivery in
    flight, so its budget is exhausted. Before this fix each batch got a fresh
    cap and the second claim would have leased another row, letting true
    in-flight concurrency to one endpoint reach the dispatcher count rather than
    the cap.

    Runs against the real (Postgres) admin DB per the no-DB-mocking rule; the
    cap is enforced identically on the portable path, so this asserts the shared
    budgeting logic.
    """
    async with admin_db.transaction() as session:
        endpoint = await WebhookEndpointRepository.create(
            session,
            name="xdispatch-cap-listener",
            secret_hash="unused",  # pragma: allowlist secret
            secret_encrypted="unused",  # pragma: allowlist secret
            target_url="https://receiver.test/hook",
            event_types=[],
            created_by="test",
        )
        endpoint_id = endpoint.id
        for i in range(2):
            event = await WebhookEventRepository.record_event(
                session,
                endpoint_id=endpoint_id,
                source_event_id=f"xcap-src-{i}",
                event_type="credential.expired",
                payload={},
            )
            assert event is not None
            await WebhookDeliveryRepository.enqueue(
                session, event_id=event.id, endpoint_id=endpoint_id
            )

    # A fixed ``moment`` shared by both claims so the first lease (pushed
    # lease_s into the future) is unambiguously *live* during the second claim.
    moment = datetime.now(UTC)
    try:
        async with admin_db.transaction() as session:
            first = await WebhookDeliveryRepository.claim_due(
                session, limit=10, now=moment, lease_s=60.0, max_in_flight_per_endpoint=1
            )
            assert len(first) == 1

        async with admin_db.transaction() as session:
            second = await WebhookDeliveryRepository.claim_due(
                session, limit=10, now=moment, lease_s=60.0, max_in_flight_per_endpoint=1
            )
            # The endpoint already has one *live* lease from the first claim, so
            # a cap of 1 leaves no budget — the second claim must take nothing.
            assert second == [], "cross-dispatcher cap must count existing live leases"

        # Sanity: with the cap disabled the still-due sibling is claimable, proving
        # it was the cap (not an empty/locked queue) that held the second claim to 0.
        async with admin_db.transaction() as session:
            uncapped = await WebhookDeliveryRepository.claim_due(
                session, limit=10, now=moment, lease_s=60.0, max_in_flight_per_endpoint=0
            )
            assert len(uncapped) == 1
    finally:
        async with admin_db.transaction() as session:
            await WebhookEndpointRepository.delete(session, endpoint_id)


# --- per-endpoint allowed_cidrs is snapshotted + threaded (Phase 3) ----------


async def test_endpoint_allowed_cidrs_threaded_to_send(admin_db: Any) -> None:
    """The endpoint's ``allowed_cidrs`` reaches the send as the request extension.

    A capturing mock transport asserts the dispatcher attaches the per-endpoint
    allowlist under the well-known extension key — the exact hook the DNS-pinning
    transport reads to enforce the allowlist on the *pinned* IP in production.
    """
    async with admin_db.transaction() as session:
        endpoint = await WebhookEndpointRepository.create(
            session,
            name="cidr-thread-listener",
            secret_hash="unused",  # pragma: allowlist secret
            secret_encrypted="unused",  # pragma: allowlist secret
            target_url="https://receiver.test/hook",
            event_types=[],
            allowed_cidrs=["10.0.0.0/8", "2001:db8::/32"],
            created_by="test",
        )
        endpoint_id = endpoint.id
        event = await WebhookEventRepository.record_event(
            session,
            endpoint_id=endpoint_id,
            source_event_id="cidr-src",
            event_type="credential.expired",
            payload={},
        )
        assert event is not None
        await WebhookDeliveryRepository.enqueue(session, event_id=event.id, endpoint_id=endpoint_id)

    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["cidrs"] = request.extensions.get(DnsPinningTransport.EXTRA_CIDRS_EXTENSION)
        return httpx.Response(200)

    try:
        await _dispatcher(admin_db, handler).drain_once()
        assert captured["cidrs"] == ["10.0.0.0/8", "2001:db8::/32"]
    finally:
        async with admin_db.transaction() as session:
            await WebhookEndpointRepository.delete(session, endpoint_id)


async def test_endpoint_without_allowed_cidrs_omits_extension(admin_db: Any) -> None:
    """No per-endpoint allowlist -> no extension attached (unchanged behaviour)."""
    async with admin_db.transaction() as session:
        endpoint = await WebhookEndpointRepository.create(
            session,
            name="no-cidr-listener",
            secret_hash="unused",  # pragma: allowlist secret
            secret_encrypted="unused",  # pragma: allowlist secret
            target_url="https://receiver.test/hook",
            event_types=[],
            created_by="test",
        )
        endpoint_id = endpoint.id
        event = await WebhookEventRepository.record_event(
            session,
            endpoint_id=endpoint_id,
            source_event_id="no-cidr-src",
            event_type="credential.expired",
            payload={},
        )
        assert event is not None
        await WebhookDeliveryRepository.enqueue(session, event_id=event.id, endpoint_id=endpoint_id)

    captured: dict[str, Any] = {"cidrs": "sentinel"}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["cidrs"] = request.extensions.get(DnsPinningTransport.EXTRA_CIDRS_EXTENSION)
        return httpx.Response(200)

    try:
        await _dispatcher(admin_db, handler).drain_once()
        assert captured["cidrs"] is None
    finally:
        async with admin_db.transaction() as session:
            await WebhookEndpointRepository.delete(session, endpoint_id)


# --- per-endpoint allowed_cidrs is ENFORCED end-to-end at send (Phase 3) -----
#
# These drive the *real* dispatcher `_send` path through the production
# `DnsPinningTransport` (not `httpx.MockTransport`, which would bypass the
# transport and so never exercise enforcement). `socket.getaddrinfo` is
# monkeypatched so the target resolves to a chosen IP; a recording inner
# transport stands in for the network so we can assert whether the pinned
# request actually reached the wire.


class _RecordingInner(httpx.AsyncBaseTransport):
    """Inner transport recording whether a (pinned) request reached the wire."""

    def __init__(self) -> None:
        self.reached = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.reached = True
        return httpx.Response(200)


def _pinning_dispatcher(
    admin_db: Any,
    inner: httpx.AsyncBaseTransport,
    *,
    secret: str | None = SECRET,
    **kwargs: Any,
) -> WebhookDeliveryDispatcher:
    """A dispatcher whose client uses the real DNS-pinning transport.

    This is the difference that matters versus ``_dispatcher``: the per-endpoint
    allowlist is only *enforced* inside ``DnsPinningTransport``, so a test that
    swaps in ``MockTransport`` proves threading but never enforcement.
    """
    return WebhookDeliveryDispatcher(
        admin_db,
        client=httpx.AsyncClient(transport=DnsPinningTransport(inner, None)),
        secret_resolver=lambda _endpoint: secret,
        **kwargs,
    )


async def _enqueue_endpoint(
    admin_db: Any,
    *,
    name: str,
    target_url: str,
    allowed_cidrs: list[str] | None,
) -> str:
    """Create an endpoint with a queued delivery; returns the endpoint id."""
    async with admin_db.transaction() as session:
        endpoint = await WebhookEndpointRepository.create(
            session,
            name=name,
            secret_hash="unused",  # pragma: allowlist secret
            secret_encrypted="unused",  # pragma: allowlist secret
            target_url=target_url,
            event_types=[],
            allowed_cidrs=allowed_cidrs,
            created_by="test",
        )
        endpoint_id = endpoint.id
        event = await WebhookEventRepository.record_event(
            session,
            endpoint_id=endpoint_id,
            source_event_id=f"e2e-{name}",
            event_type="credential.expired",
            payload={},
        )
        assert event is not None
        await WebhookDeliveryRepository.enqueue(session, event_id=event.id, endpoint_id=endpoint_id)
    return endpoint_id


async def test_out_of_range_allowlist_blocks_delivery_end_to_end(
    admin_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reported bug: allowlist ``192.0.2.1/32`` + target resolving elsewhere = BLOCKED.

    Drives the real dispatcher → ``_send`` → ``DnsPinningTransport`` path. The
    target ``receiver.test`` resolves to a public IP that is NOT in the endpoint's
    ``allowed_cidrs``, so the pinned connection must be refused: the request never
    reaches the wire and the delivery is recorded as a (categorized) egress
    failure — not succeeded.
    """

    def _resolves_to(host: str, *_a: Any, **_k: Any) -> list[Any]:
        # A public IP distinct from the allowlisted 192.0.2.1 (like Slack's real IP).
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _resolves_to)

    endpoint_id = await _enqueue_endpoint(
        admin_db,
        name="e2e-out-of-range",
        target_url="https://receiver.test/hook",
        allowed_cidrs=["192.0.2.1/32"],
    )
    inner = _RecordingInner()
    try:
        attempted = await _pinning_dispatcher(admin_db, inner).drain_once()
        assert attempted == 1

        row = await _row(admin_db, endpoint_id)
        assert inner.reached is False, "a blocked send must never reach the wire"
        assert row.status == STATUS_FAILED
        assert row.last_status_code is None
        # The allowlist refusal surfaces as an AllowlistBlockedError from the
        # transport, which the dispatcher categorises to its own safe reason —
        # distinct from a generic egress-policy block, and never leaking the
        # pinned IP or the offending CIDR.
        assert row.last_error == "blocked_by_allowlist"
        assert "93.184" not in (row.last_error or "")
        assert "192.0.2" not in (row.last_error or "")
    finally:
        async with admin_db.transaction() as session:
            await WebhookEndpointRepository.delete(session, endpoint_id)


async def test_in_range_allowlist_permits_delivery_end_to_end(
    admin_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same endpoint, but the target resolves to the allowlisted IP -> SUCCEEDS.

    The complement of the block test: with the resolved IP inside
    ``allowed_cidrs`` the pinned request reaches the wire and the delivery is
    marked succeeded.
    """

    def _resolves_to(host: str, *_a: Any, **_k: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("192.0.2.1", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _resolves_to)

    endpoint_id = await _enqueue_endpoint(
        admin_db,
        name="e2e-in-range",
        target_url="https://receiver.test/hook",
        allowed_cidrs=["192.0.2.1/32"],
    )
    inner = _RecordingInner()
    try:
        await _pinning_dispatcher(admin_db, inner).drain_once()

        row = await _row(admin_db, endpoint_id)
        assert inner.reached is True, "an allowlisted in-range send must reach the wire"
        assert row.status == STATUS_SUCCEEDED
        assert row.last_status_code == 200
    finally:
        async with admin_db.transaction() as session:
            await WebhookEndpointRepository.delete(session, endpoint_id)


async def test_empty_allowlist_permits_public_delivery_end_to_end(
    admin_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No allowlist -> unrestricted: a public target still delivers (no regression)."""

    def _resolves_to(host: str, *_a: Any, **_k: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _resolves_to)

    endpoint_id = await _enqueue_endpoint(
        admin_db,
        name="e2e-empty-allowlist",
        target_url="https://receiver.test/hook",
        allowed_cidrs=[],
    )
    inner = _RecordingInner()
    try:
        await _pinning_dispatcher(admin_db, inner).drain_once()

        row = await _row(admin_db, endpoint_id)
        assert inner.reached is True
        assert row.status == STATUS_SUCCEEDED
    finally:
        async with admin_db.transaction() as session:
            await WebhookEndpointRepository.delete(session, endpoint_id)


async def test_metadata_still_denied_end_to_end_even_when_allowlisted(
    admin_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The metadata hard-deny survives at send even if a covering CIDR is allowlisted."""

    def _resolves_to(host: str, *_a: Any, **_k: Any) -> list[Any]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("169.254.169.254", 0))
        ]

    monkeypatch.setattr(socket, "getaddrinfo", _resolves_to)

    endpoint_id = await _enqueue_endpoint(
        admin_db,
        name="e2e-metadata-denied",
        target_url="https://receiver.test/hook",
        allowed_cidrs=["169.254.0.0/16"],
    )
    inner = _RecordingInner()
    try:
        await _pinning_dispatcher(admin_db, inner).drain_once()

        row = await _row(admin_db, endpoint_id)
        assert inner.reached is False, "metadata must never reach the wire, allowlist or not"
        assert row.status == STATUS_FAILED
        assert row.last_status_code is None
        # The metadata hard-deny is an egress-policy block, *not* an allowlist
        # block — the categoriser must attribute it to the policy so the two read
        # differently in the UI, and it must not leak the metadata IP.
        assert row.last_error == "blocked_egress_policy"
        assert "169.254" not in (row.last_error or "")
    finally:
        async with admin_db.transaction() as session:
            await WebhookEndpointRepository.delete(session, endpoint_id)
