"""Unit tests for the execution record's shared ``error`` vocabulary."""

from __future__ import annotations

import pytest

from jentic_one.broker.core.exceptions import (
    BrokerError,
    CircuitOpenError,
    DeadlineExceededError,
    UpstreamBodyInterruptedError,
    UpstreamResponseTooLargeError,
    UpstreamTimeoutError,
)
from jentic_one.broker.core.execution import execution_error


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (UpstreamTimeoutError("t", type="upstream_connect_failed"), "upstream_connect_failed"),
        (UpstreamTimeoutError("t"), "upstream_timeout"),
        (BrokerError("x", type="upstream_transport_error"), "upstream_transport_error"),
        (CircuitOpenError("open", type="circuit_open"), "circuit_open"),
        (DeadlineExceededError("late"), "deadline_exceeded"),
        (UpstreamResponseTooLargeError("big"), "upstream_response_too_large"),
        (
            UpstreamBodyInterruptedError("drop", transport_error="RemoteProtocolError"),
            "upstream_transport_error",
        ),
        # An untyped broker error falls back to its class, never ``about:blank``.
        (CircuitOpenError("open"), "CircuitOpenError"),
        (RuntimeError("secret-bearing message"), "server_error: RuntimeError"),
    ],
)
def test_execution_error_is_the_problem_type_never_the_message(
    exc: BaseException, expected: str
) -> None:
    assert execution_error(exc) == expected
