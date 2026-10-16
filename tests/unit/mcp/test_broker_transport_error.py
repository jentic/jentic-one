"""Unit tests for the broker transport-failure classifier (#1531).

The Python twin of ``cli/internal/agentops/broker_transport_test.go``: the
problem-type → retryable mapping, the Retry-After relay, and the origin-header
boundary that keeps a mirrored vendor 5xx a normal result.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any

import httpx
import pytest

from jentic_one.broker.core.exceptions import (
    CircuitOpenError,
    CredentialRefreshTransientError,
    DeadlineExceededError,
    RunnerSchemeUnsupportedError,
    RunnerUnavailableError,
    UpstreamResponseTooLargeError,
    UpstreamTimeoutError,
)
from jentic_one.broker.core.problem import status_for_broker_error
from jentic_one.mcp import execute as ex
from jentic_one.mcp.envelopes import CODE_TRANSPORT_ERROR


def _headers(origin: str | None, retry_after: str | None = None) -> httpx.Headers:
    h: dict[str, str] = {"Content-Type": "application/problem+json"}
    if origin is not None:
        h["Jentic-Error-Origin"] = origin
    if retry_after is not None:
        h["Retry-After"] = retry_after
    return httpx.Headers(h)


def _body(problem: dict[str, Any]) -> bytes:
    return json.dumps(problem).encode()


@pytest.mark.parametrize(
    ("exc", "retry_safe", "want_retryable", "want_recovery"),
    [
        (CircuitOpenError("open", type="circuit_open"), False, True, ex.RECOVER_WAIT),
        (RunnerUnavailableError("down", type="runner_unavailable"), False, True, ex.RECOVER_WAIT),
        (
            CredentialRefreshTransientError("cred_1: Timeout", type="refresh_transient_error"),
            False,
            True,
            ex.RECOVER_WAIT,
        ),
        (UpstreamTimeoutError("slow"), False, False, ex.RECOVER_TIMEOUT),
        (UpstreamTimeoutError("slow"), True, True, ex.RECOVER_TIMEOUT),
        (DeadlineExceededError("late", type="deadline_exceeded"), True, True, ex.RECOVER_TIMEOUT),
        (
            RunnerSchemeUnsupportedError("ftp", type="runner_scheme_unsupported"),
            True,
            False,
            ex.RECOVER_SCHEME,
        ),
        (UpstreamResponseTooLargeError("too big"), True, False, ex.RECOVER_UNKNOWN),
    ],
    ids=lambda v: type(v).__name__ if isinstance(v, Exception) else str(v),
)
def test_broker_problem_types_map_to_their_retryable_hint(
    exc, retry_safe, want_retryable, want_recovery
) -> None:
    """Each named BrokerError, at the status and wire ``type`` the broker
    actually emits it with, gets the documented retryable hint."""
    status = status_for_broker_error(exc)
    got = ex.classify_broker_transport_error(
        status,
        _headers("broker"),
        _body({"type": exc.type, "title": exc.detail, "status": status}),
        retry_safe=retry_safe,
    )
    assert got is not None
    assert got.code == CODE_TRANSPORT_ERROR
    assert ex.broker_transport_recovery(status, exc.type) == want_recovery
    assert got.extra == {"retryable": want_retryable}
    assert got.details is not None
    assert got.details["retryable"] is want_retryable
    assert got.details["http_status"] == status
    assert got.details["origin"] == "broker"
    assert exc.detail in got.message


@pytest.mark.parametrize("problem_type", ["streaming_unsupported", "about:blank", ""])
def test_unknown_broker_5xx_defaults_to_not_retryable(problem_type) -> None:
    """A 500/502 whose type does not say the upstream was never reached is
    never advertised as retryable, even for a retry-safe call."""
    got = ex.classify_broker_transport_error(
        500,
        _headers("broker"),
        _body({"type": problem_type, "title": "boom"}),
        retry_safe=True,
    )
    assert got is not None
    assert got.extra == {"retryable": False}
    assert got.next_tool == "get_started"
    assert "Do not re-send" in got.actionable


def test_non_json_broker_5xx_falls_back_to_the_status_phrase() -> None:
    got = ex.classify_broker_transport_error(
        502, _headers("broker"), b"Bad Gateway", retry_safe=False
    )
    assert got is not None
    assert got.message.endswith("(HTTP 502): Bad Gateway")
    assert got.details == {"http_status": 502, "origin": "broker", "retryable": False}


def test_rate_limit_429_relays_retry_after() -> None:
    got = ex.classify_broker_transport_error(
        429,
        _headers("broker", retry_after="7"),
        _body({"type": "rate_limit_exceeded", "title": "Rate limit exceeded"}),
        retry_safe=False,
    )
    assert got is not None
    assert got.extra == {"retryable": True, "retry_after_s": 7}
    assert got.details is not None
    assert got.details["retry_after_s"] == 7
    assert "(7s)" in got.actionable


def test_scheme_unsupported_points_at_search_apis() -> None:
    got = ex.classify_broker_transport_error(
        501,
        _headers("broker"),
        _body({"type": "runner_scheme_unsupported", "title": "no ftp runner"}),
        retry_safe=True,
    )
    assert got is not None
    assert got.next_tool == "search_apis"


@pytest.mark.parametrize(
    ("origin", "status"),
    [
        ("upstream", 503),
        ("upstream", 500),
        ("upstream", 504),
        ("upstream", 429),
        (None, 502),
        ("", 503),
    ],
)
def test_non_broker_origin_passes_through(origin, status) -> None:
    """The header decides: a mirrored vendor 5xx/429 (or no origin) is never
    classified, even with a broker-shaped body and a Retry-After."""
    assert (
        ex.classify_broker_transport_error(
            status,
            _headers(origin, retry_after="5"),
            _body({"type": "circuit_open"}),
            retry_safe=True,
        )
        is None
    )


@pytest.mark.parametrize("status", [200, 202, 400, 403, 404, 422])
def test_non_5xx_broker_statuses_are_not_transport_errors(status) -> None:
    assert (
        ex.classify_broker_transport_error(
            status, _headers("broker"), _body({"type": "x"}), retry_safe=True
        )
        is None
    )


def test_origin_header_is_case_and_space_tolerant() -> None:
    got = ex.classify_broker_transport_error(
        503, _headers(" Broker "), _body({"type": "circuit_open"}), retry_safe=False
    )
    assert got is not None


def test_retry_after_seconds() -> None:
    future = format_datetime(datetime.now(UTC) + timedelta(seconds=90), usegmt=True)
    past = format_datetime(datetime.now(UTC) - timedelta(hours=1), usegmt=True)
    assert ex.retry_after_seconds(httpx.Headers({})) is None
    assert ex.retry_after_seconds(httpx.Headers({"Retry-After": "12"})) == 12
    assert ex.retry_after_seconds(httpx.Headers({"Retry-After": " 3 "})) == 3
    assert ex.retry_after_seconds(httpx.Headers({"Retry-After": "1.5"})) == 1.5
    assert ex.retry_after_seconds(httpx.Headers({"Retry-After": "-1"})) is None
    assert ex.retry_after_seconds(httpx.Headers({"Retry-After": "soon"})) is None
    remaining = ex.retry_after_seconds(httpx.Headers({"Retry-After": future}))
    assert remaining is not None
    assert 60 < remaining <= 91
    assert ex.retry_after_seconds(httpx.Headers({"Retry-After": past})) == 0
