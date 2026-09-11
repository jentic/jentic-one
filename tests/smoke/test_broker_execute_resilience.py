"""Smoke tests: broker resilience under upstream faults.

Drives the harness ``X-Mock-*`` fault-injection middleware *through the broker*
(the broker relays the headers verbatim) to pin how the broker surfaces upstream
status passthrough, timeouts, and transport failures.

Every proxied call resolves a credential regardless of the op's ``security``
block (the broker injects for every op, 424 otherwise), so all tests use the
``executable_harness`` fixture (one directly bound bearer credential) and an op that
needs no *upstream* auth (``/behavior/echo``) to isolate the resilience
dimension.

Each status-sequence/cursor-stateful header carries a fresh ``X-Mock-Test-Id``
(uuid) — the harness keeps per-test-id cursors in-process with no network reset,
so a reused id would bleed across tests.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.smoke.conftest import (
    ExecutableHarness,
    _skip_if_no_admin_surface,
    broker_call,
)


@pytest.mark.smoke
def test_mock_status_passthrough(
    broker_url: str,
    executable_harness: ExecutableHarness,
    upstream_incluster_url: str,
) -> None:
    """``X-Mock-Status: 503`` → the broker surfaces the upstream 503 to the client."""
    _skip_if_no_admin_surface()
    raw, status, _ = broker_call(
        broker_url,
        f"{upstream_incluster_url}/behavior/echo",
        method="POST",
        token=executable_harness.agent_token,
        headers={"X-Mock-Status": "503"},
    )
    assert status == 503, f"expected upstream 503 surfaced, got {status}: {raw!r}"


@pytest.mark.smoke
def test_mock_delay_within_timeout(
    broker_url: str,
    executable_harness: ExecutableHarness,
    upstream_incluster_url: str,
) -> None:
    """``X-Mock-Delay: 1`` (under the broker upstream timeout) → 200 round trips."""
    _skip_if_no_admin_surface()
    raw, status, _ = broker_call(
        broker_url,
        f"{upstream_incluster_url}/behavior/echo",
        method="POST",
        token=executable_harness.agent_token,
        headers={"X-Mock-Delay": "1"},
        client_timeout=15.0,
    )
    assert status == 200, f"expected 200 under timeout, got {status}: {raw!r}"


@pytest.mark.smoke
def test_mock_delay_exceeds_timeout(
    broker_url: str,
    executable_harness: ExecutableHarness,
    upstream_incluster_url: str,
    broker_upstream_timeout_s: float,
) -> None:
    """Delay past the broker upstream timeout → broker 504 (no hang).

    The harness sleeps before responding; the broker's upstream timeout fires and
    maps to ``UpstreamTimeoutError`` → 504. The test runner waits a margin beyond
    the broker timeout so the *broker* (not urllib) is what times out.

    CI budget note: wall-clock is ``broker_upstream_timeout_s + 35s`` (65s at
    default). Lower via the ``BROKER_UPSTREAM_TIMEOUT_S`` env var if the broker
    overlay reduces ``broker.upstream_timeout_s``.
    """
    _skip_if_no_admin_surface()
    delay = broker_upstream_timeout_s + 5
    raw, status, _ = broker_call(
        broker_url,
        f"{upstream_incluster_url}/behavior/echo",
        method="POST",
        token=executable_harness.agent_token,
        headers={"X-Mock-Delay": str(delay)},
        client_timeout=delay + 30,
    )
    assert status == 504, f"expected broker 504 gateway timeout, got {status}: {raw!r}"


@pytest.mark.smoke
def test_mock_status_sequence_first_status(
    broker_url: str,
    executable_harness: ExecutableHarness,
    upstream_incluster_url: str,
) -> None:
    """``X-Mock-Status-Sequence: 503,503,200`` → first observed status is 503.

    The broker has no automatic upstream-status retry in the proxy path (the only
    "retry" machinery is idempotency/admission, not status-based), so a single
    call observes the first element of the sequence. If broker retry-on-503 is
    ever enabled, this assertion (and the comment) must change to expect 200.
    """
    _skip_if_no_admin_surface()
    test_id = uuid.uuid4().hex
    _, status, _ = broker_call(
        broker_url,
        f"{upstream_incluster_url}/behavior/echo",
        method="POST",
        token=executable_harness.agent_token,
        headers={"X-Mock-Status-Sequence": "503,503,200", "X-Mock-Test-Id": test_id},
    )
    assert status == 503, f"expected first sequence status 503 (no broker retry), got {status}"


@pytest.mark.smoke
def test_mock_disconnect_maps_to_error(
    broker_url: str,
    executable_harness: ExecutableHarness,
    upstream_incluster_url: str,
) -> None:
    """``X-Mock-Disconnect: true`` → broker maps the mid-stream failure to an error.

    The harness aborts the response body mid-stream (``RemoteProtocolError``); the
    broker's runner catches the transport error and raises a ``BrokerError`` (→ 500
    error envelope). The contract under test is "no hang, a clean error envelope,
    not a raw stacktrace" — assert a 5xx with the broker's problem+json shape.
    """
    _skip_if_no_admin_surface()
    raw, status, _ = broker_call(
        broker_url,
        f"{upstream_incluster_url}/behavior/echo",
        method="POST",
        token=executable_harness.agent_token,
        headers={"X-Mock-Disconnect": "true"},
        client_timeout=20.0,
    )
    assert 500 <= status < 600, f"expected broker 5xx for transport failure, got {status}: {raw!r}"
    body = json.loads(raw)
    assert "error_origin" in body, f"expected problem+json envelope, got {body}"
