"""Unit tests for the broker transport-failure classifier (#1531).

The Python twin of ``cli/internal/agentops/broker_transport_test.go``: the
problem-type → retryable mapping, the Retry-After relay, and the origin-header
boundary that keeps a mirrored vendor 5xx a normal result.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any, cast

import httpx
import pytest

from jentic_one.broker.adapters.runners.base import RunnerRequest
from jentic_one.broker.adapters.runners.deadline import DeadlineRunner
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.core.exceptions import (
    BrokerError,
    CircuitOpenError,
    CredentialRefreshTransientError,
    DeadlineExceededError,
    RunnerSchemeUnsupportedError,
    RunnerUnavailableError,
    TooManyCandidatesError,
    UpstreamResponseTooLargeError,
    UpstreamTimeoutError,
    UpstreamTransportError,
)
from jentic_one.broker.core.problem import status_for_broker_error
from jentic_one.broker.web.errors import handle_broker_error
from jentic_one.mcp import execute as ex
from jentic_one.mcp.envelopes import CODE_TRANSPORT_ERROR, ToolError


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
        (TooManyCandidatesError("too many"), False, True, ex.RECOVER_WAIT),
        (CredentialRefreshTransientError("cred_1: Timeout"), False, True, ex.RECOVER_WAIT),
        (
            UpstreamTimeoutError("no connect", type="upstream_connect_failed", pre_send=True),
            False,
            True,
            ex.RECOVER_WAIT,
        ),
        (UpstreamTimeoutError("slow"), False, False, ex.RECOVER_TIMEOUT),
        (UpstreamTimeoutError("slow"), True, True, ex.RECOVER_TIMEOUT),
        (DeadlineExceededError("late"), True, True, ex.RECOVER_TIMEOUT),
        (
            RunnerSchemeUnsupportedError("ftp", type="runner_scheme_unsupported"),
            True,
            False,
            ex.RECOVER_SCHEME,
        ),
        (UpstreamResponseTooLargeError("too big"), True, False, ex.RECOVER_UNKNOWN),
        (
            UpstreamTransportError("refused", type="upstream_connect_failed", pre_send=True),
            False,
            True,
            ex.RECOVER_WAIT,
        ),
        (UpstreamTransportError("dropped"), True, False, ex.RECOVER_UNKNOWN),
        (
            BrokerError("bad header", type="upstream_request_invalid"),
            True,
            False,
            ex.RECOVER_UNKNOWN,
        ),
    ],
    ids=lambda v: type(v).__name__ if isinstance(v, Exception) else str(v),
)
def test_broker_problem_types_map_to_their_retryable_hint(
    exc, retry_safe, want_retryable, want_recovery
) -> None:
    """Each named BrokerError, at the status, wire ``type`` and origin the broker
    actually emits it with, gets the documented retryable hint."""
    status = status_for_broker_error(exc)
    assert exc.origin.value == "broker"
    got = ex.classify_broker_transport_error(
        status,
        _headers(exc.origin.value),
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


@pytest.mark.parametrize(
    ("status", "problem_type", "want"),
    [
        (503, "circuit_open", ex.RECOVER_WAIT),
        (503, "runner_unavailable", ex.RECOVER_WAIT),
        (503, "server_at_capacity", ex.RECOVER_WAIT),
        (503, "too_many_candidates", ex.RECOVER_WAIT),
        (502, "refresh_transient_error", ex.RECOVER_WAIT),
        (504, "upstream_connect_failed", ex.RECOVER_WAIT),
        (502, "upstream_connect_failed", ex.RECOVER_WAIT),
        (429, "rate_limit_exceeded", ex.RECOVER_WAIT),
        (429, "", ex.RECOVER_WAIT),
        (504, "upstream_timeout", ex.RECOVER_TIMEOUT),
        (504, "deadline_exceeded", ex.RECOVER_TIMEOUT),
        (501, "runner_scheme_unsupported", ex.RECOVER_SCHEME),
        (502, "upstream_response_too_large", ex.RECOVER_UNKNOWN),
        (502, "upstream_transport_error", ex.RECOVER_UNKNOWN),
        (500, "upstream_request_invalid", ex.RECOVER_UNKNOWN),
        (504, "about:blank", ex.RECOVER_UNKNOWN),
        (504, "", ex.RECOVER_UNKNOWN),
        (503, "about:blank", ex.RECOVER_UNKNOWN),
        (500, "streaming_unsupported", ex.RECOVER_UNKNOWN),
    ],
)
def test_broker_transport_recovery_parity(status, problem_type, want) -> None:
    """The shared routing table: ``TestBrokerTransportRecoveryForParity`` in
    ``cli/internal/agentops/broker_transport_test.go`` asserts the same rows,
    so the CLI and the MCP mount classify every broker problem type alike."""
    assert ex.broker_transport_recovery(status, problem_type) == want


@pytest.mark.parametrize(
    ("method", "key", "want"),
    [
        ("GET", "", True),
        ("HEAD", "", True),
        ("POST", "", False),
        ("POST", "idem-1", True),
        ("DELETE", "", False),
        # Parity with agentops.RetrySafe (TestRetrySafe).
        ("get", "", True),
        ("head", "", True),
        ("POST", "   ", False),
        ("POST", "\tidem-1\n", True),
        ("PUT", "\t", False),
        ("POST", None, False),
    ],
)
def test_retry_safe(method, key, want) -> None:
    assert ex.retry_safe(method, key) is want


@pytest.mark.parametrize(
    ("headers", "idempotency_key", "want"),
    [
        ([], None, ""),
        ([], "idem-arg", "idem-arg"),
        ([("Idempotency-Key", "idem-hdr")], None, "idem-hdr"),
        ([(" idempotency-key ", " idem-hdr ")], "idem-arg", "idem-hdr"),
        ([("X-Other", "v")], "idem-arg", "idem-arg"),
    ],
)
def test_effective_idempotency_key_mirrors_the_wire(headers, idempotency_key, want) -> None:
    """A caller-supplied header wins over ``idempotency_key``, as in
    ``send_to_broker`` (Go reads the built request's header)."""
    assert ex.effective_idempotency_key(headers, idempotency_key) == want


# --- Real broker wire shape (#1675) -----------------------------------------
# The cases above feed the classifier hand-written problem bodies. These raise
# each broker-generated 5xx from its real raise site (the HTTP runner, the
# deadline runner), render it through the broker's own problem+json handler,
# and classify THAT response, so the origin stamping and the problem types the
# classifier keys on cannot drift apart again.


async def _raise_from_runner(
    handler: Any, *, method: str = "POST", max_response_bytes: int = 0, deadline_s: float = 0
) -> BrokerError:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        runner: Any = HttpRunner(client, max_response_bytes=max_response_bytes)
        if deadline_s:
            runner = DeadlineRunner(runner, deadline_s=deadline_s)
        with pytest.raises(BrokerError) as excinfo:
            await runner.run(RunnerRequest(method=method, url="https://api.example.com/x"))
    return excinfo.value


async def _classify_rendered(exc: BrokerError, *, retry_safe: bool) -> ToolError | None:
    resp = await handle_broker_error(cast(Any, None), exc)
    return ex.classify_broker_transport_error(
        resp.status_code,
        httpx.Headers(dict(resp.headers)),
        bytes(resp.body),
        retry_safe=retry_safe,
    )


def _connect_timeout(_req: httpx.Request) -> httpx.Response:
    raise httpx.ConnectTimeout("slow")


def _pool_timeout(_req: httpx.Request) -> httpx.Response:
    raise httpx.PoolTimeout("pool exhausted")


def _read_timeout(_req: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("slow")


def _connect_refused(_req: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused")


def _proxy_refused(_req: httpx.Request) -> httpx.Response:
    raise httpx.ProxyError("tunnel refused")


def _protocol_error(_req: httpx.Request) -> httpx.Response:
    raise httpx.RemoteProtocolError("peer closed")


def _local_protocol_error(_req: httpx.Request) -> httpx.Response:
    raise httpx.LocalProtocolError("bad header")


def _oversized(_req: httpx.Request) -> httpx.Response:
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(8):
            yield b"x" * 8

    return httpx.Response(200, content=chunks())


async def _stalls(_req: httpx.Request) -> httpx.Response:
    await asyncio.sleep(1)
    return httpx.Response(200)  # pragma: no cover - the deadline fires first


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "kwargs", "status", "problem_type", "retry_safe", "want_retryable"),
    [
        (_connect_timeout, {}, 504, "upstream_connect_failed", False, True),
        (_pool_timeout, {}, 504, "upstream_connect_failed", False, True),
        (_connect_refused, {}, 502, "upstream_connect_failed", False, True),
        (_proxy_refused, {}, 502, "upstream_connect_failed", False, True),
        (_read_timeout, {}, 504, "upstream_timeout", False, False),
        (_read_timeout, {}, 504, "upstream_timeout", True, True),
        (_protocol_error, {}, 502, "upstream_transport_error", True, False),
        (_local_protocol_error, {}, 500, "upstream_request_invalid", True, False),
        (_oversized, {"max_response_bytes": 8}, 502, "upstream_response_too_large", True, False),
        (_stalls, {"deadline_s": 0.01}, 504, "deadline_exceeded", True, True),
    ],
    ids=[
        "connect",
        "pool-timeout",
        "connect-refused",
        "proxy-refused",
        "read-unsafe",
        "read-safe",
        "transport",
        "request-invalid",
        "too-large",
        "deadline",
    ],
)
async def test_real_broker_5xx_is_a_transport_error(
    handler, kwargs, status, problem_type, retry_safe, want_retryable
) -> None:
    exc = await _raise_from_runner(handler, **kwargs)
    got = await _classify_rendered(exc, retry_safe=retry_safe)
    assert got is not None, "a broker-generated 5xx must not pass through as an upstream answer"
    assert got.code == CODE_TRANSPORT_ERROR
    assert got.details is not None
    assert got.details["http_status"] == status
    assert got.details["problem_type"] == problem_type
    assert got.details["retryable"] is want_retryable


@pytest.mark.asyncio
async def test_real_refresh_transient_502_is_a_transport_error() -> None:
    """The orchestrator raises it with only ``detail``; the class supplies the
    type and the broker origin."""
    got = await _classify_rendered(
        CredentialRefreshTransientError("cred_1: TimeoutError"), retry_safe=False
    )
    assert got is not None
    assert got.details is not None
    assert got.details["problem_type"] == "refresh_transient_error"
    assert got.details["retryable"] is True
