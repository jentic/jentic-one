"""Upstream transport failures map to 502 Bad Gateway on both runner paths (#1706).

A transport failure the broker hits while talking to the upstream (a dropped
connection, a mid-body disconnect, a refused connect) is a gateway failure, so
the broker answers ``502`` — not the ``500`` that reads as a broker bug. A
connect or read *timeout* stays ``504``. Each case raises from the real
``HttpRunner`` and renders through the broker's own problem+json handler, on the
buffered (``run``) and streaming (``stream``) paths alike. The retry and circuit
decorators keep treating these failures exactly as before.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any, cast

import httpx
import pytest

from jentic_one.broker.adapters.runners.base import RunnerRequest
from jentic_one.broker.adapters.runners.circuit import CircuitBreakerRunner
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.adapters.runners.retry import RetryRunner
from jentic_one.broker.core.exceptions import BrokerError, CircuitOpenError
from jentic_one.broker.core.problem import status_for_broker_error
from jentic_one.broker.web.errors import handle_broker_error
from jentic_one.shared.resilience import CircuitBreaker
from jentic_one.shared.state.backend import MemoryStateBackend

_URL = "https://api.example.com/x"
_MARKER = "MARKER_IN_EXCEPTION_MESSAGE"

Handler = Callable[[httpx.Request], httpx.Response]


def _raises(exc_type: type[httpx.HTTPError]) -> Handler:
    def handle(_req: httpx.Request) -> httpx.Response:
        raise exc_type(f"boom {_MARKER}")

    return handle


def _drops_mid_body(_req: httpx.Request) -> httpx.Response:
    """Headers arrive, then the upstream drops the connection partway through the body."""

    async def body() -> AsyncIterator[bytes]:
        yield b'{"partial": '
        raise httpx.RemoteProtocolError(f"peer closed connection {_MARKER}")

    return httpx.Response(200, content=body())


async def _buffered_error(handler: Handler, *, method: str = "POST") -> BrokerError:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BrokerError) as excinfo:
            await HttpRunner(client).run(RunnerRequest(method=method, url=_URL))
    return excinfo.value


async def _streaming_error(handler: Handler, *, method: str = "POST") -> BrokerError:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BrokerError) as excinfo:
            async with HttpRunner(client).stream(RunnerRequest(method=method, url=_URL)):
                pass  # pragma: no cover - the stream never opens
    return excinfo.value


async def _rendered(exc: BrokerError) -> tuple[int, dict[str, Any], httpx.Headers]:
    resp = await handle_broker_error(cast(Any, None), exc)
    return resp.status_code, json.loads(bytes(resp.body)), httpx.Headers(dict(resp.headers))


# --- buffered path ----------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc_type",
    [httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError, httpx.ProxyError],
    ids=lambda t: t.__name__,
)
async def test_buffered_transport_error_is_502(exc_type: type[httpx.HTTPError]) -> None:
    exc = await _buffered_error(_raises(exc_type))

    status, body, headers = await _rendered(exc)
    assert status == 502
    assert body["status"] == 502
    assert body["type"] == "upstream_transport_error"
    assert body["detail"] == f"Upstream transport error ({exc_type.__name__})"
    assert body["error_origin"] == "broker"
    assert headers["content-type"] == "application/problem+json"
    assert headers["jentic-error-origin"] == "broker"
    assert _MARKER not in json.dumps(body)
    # Post-send: bytes may have reached the upstream, so only an idempotent
    # call may be retried.
    assert exc.pre_send is False


@pytest.mark.asyncio
async def test_buffered_mid_body_disconnect_is_502() -> None:
    """The #1706 reproduction: the upstream drops mid-body (``X-Mock-Disconnect``)."""
    exc = await _buffered_error(_drops_mid_body)

    status, body, _ = await _rendered(exc)
    assert status == 502
    assert body["type"] == "upstream_transport_error"
    assert body["detail"] == "Upstream transport error (RemoteProtocolError)"
    assert body["error_origin"] == "broker"
    assert _MARKER not in json.dumps(body)


@pytest.mark.asyncio
async def test_buffered_connect_refused_is_502_and_pre_send() -> None:
    exc = await _buffered_error(_raises(httpx.ConnectError))

    status, body, _ = await _rendered(exc)
    assert status == 502
    assert body["type"] == "upstream_connect_failed"
    assert body["error_origin"] == "broker"
    assert _MARKER not in json.dumps(body)
    # Connect phase: no request bytes were sent, so a retry is safe for any method.
    assert exc.pre_send is True


@pytest.mark.asyncio
async def test_buffered_connect_timeout_stays_504() -> None:
    exc = await _buffered_error(_raises(httpx.ConnectTimeout))

    assert status_for_broker_error(exc) == 504
    assert exc.type == "upstream_connect_failed"
    assert exc.pre_send is True


@pytest.mark.asyncio
async def test_buffered_read_timeout_stays_504() -> None:
    exc = await _buffered_error(_raises(httpx.ReadTimeout))

    assert status_for_broker_error(exc) == 504
    assert exc.type == "upstream_timeout"
    assert exc.pre_send is False


@pytest.mark.asyncio
async def test_buffered_local_protocol_error_stays_500() -> None:
    """A malformed outbound request is the broker's own failure, not the upstream's."""
    exc = await _buffered_error(_raises(httpx.LocalProtocolError))

    assert status_for_broker_error(exc) == 500
    assert exc.type == "upstream_transport_error"


# --- streaming path (failure before the response starts) --------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc_type",
    [httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError, httpx.ProxyError],
    ids=lambda t: t.__name__,
)
async def test_streaming_transport_error_is_502(exc_type: type[httpx.HTTPError]) -> None:
    exc = await _streaming_error(_raises(exc_type))

    status, body, _ = await _rendered(exc)
    assert status == 502
    assert body["type"] == "upstream_transport_error"
    assert body["detail"] == f"Upstream transport error ({exc_type.__name__})"
    assert body["error_origin"] == "broker"
    assert _MARKER not in json.dumps(body)
    assert exc.pre_send is False


@pytest.mark.asyncio
async def test_streaming_connect_refused_is_502_and_pre_send() -> None:
    exc = await _streaming_error(_raises(httpx.ConnectError))

    assert status_for_broker_error(exc) == 502
    assert exc.type == "upstream_connect_failed"
    assert exc.pre_send is True


@pytest.mark.asyncio
async def test_streaming_connect_timeout_matches_buffered() -> None:
    exc = await _streaming_error(_raises(httpx.ConnectTimeout))

    assert status_for_broker_error(exc) == 504
    assert exc.type == "upstream_connect_failed"
    assert exc.pre_send is True


@pytest.mark.asyncio
async def test_streaming_local_protocol_error_stays_500() -> None:
    exc = await _streaming_error(_raises(httpx.LocalProtocolError))

    assert status_for_broker_error(exc) == 500
    assert exc.type == "upstream_transport_error"


# --- retry / circuit semantics are unchanged --------------------------------


class _CountingTransport(httpx.AsyncBaseTransport):
    """Counts requests and fails every one with ``exc_type``."""

    def __init__(self, exc_type: type[httpx.HTTPError]) -> None:
        self.exc_type = exc_type
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        raise self.exc_type("boom")


@pytest.fixture()
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant(_delay: float) -> None:
        return None

    monkeypatch.setattr("jentic_one.broker.adapters.runners.retry.asyncio.sleep", _instant)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_sleep")
@pytest.mark.parametrize(
    ("exc_type", "method", "want_calls"),
    [
        # Post-send failure: retried only for an idempotent method.
        (httpx.RemoteProtocolError, "GET", 3),
        (httpx.RemoteProtocolError, "POST", 1),
        (httpx.ReadError, "GET", 3),
        (httpx.ReadError, "POST", 1),
        # Connect phase (pre_send): retried for any method.
        (httpx.ConnectError, "GET", 3),
        (httpx.ConnectError, "POST", 3),
    ],
)
async def test_retry_gate_unchanged(
    exc_type: type[httpx.HTTPError], method: str, want_calls: int
) -> None:
    transport = _CountingTransport(exc_type)
    async with httpx.AsyncClient(transport=transport) as client:
        runner = RetryRunner(HttpRunner(client), max_attempts=3, base_backoff_s=0.0)
        with pytest.raises(BrokerError) as excinfo:
            await runner.run(RunnerRequest(method=method, url=_URL))
    assert transport.calls == want_calls
    assert status_for_broker_error(excinfo.value) == 502


def _breaker() -> CircuitBreaker:
    return CircuitBreaker(
        MemoryStateBackend(), failure_ratio=0.5, min_calls=2, window_s=30, cooldown_s=15
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc_type",
    [httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError],
    ids=lambda t: t.__name__,
)
async def test_circuit_counts_transport_error_as_failure(
    exc_type: type[httpx.HTTPError],
) -> None:
    async with httpx.AsyncClient(transport=_CountingTransport(exc_type)) as client:
        runner = CircuitBreakerRunner(HttpRunner(client), _breaker())
        for _ in range(2):
            with pytest.raises(BrokerError) as excinfo:
                await runner.run(RunnerRequest(method="GET", url=_URL))
            assert status_for_broker_error(excinfo.value) == 502
        with pytest.raises(CircuitOpenError):
            await runner.run(RunnerRequest(method="GET", url=_URL))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc_type",
    [httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError],
    ids=lambda t: t.__name__,
)
async def test_circuit_counts_streaming_open_failure(exc_type: type[httpx.HTTPError]) -> None:
    async with httpx.AsyncClient(transport=_CountingTransport(exc_type)) as client:
        runner = CircuitBreakerRunner(HttpRunner(client), _breaker())
        for _ in range(2):
            with pytest.raises(BrokerError) as excinfo:
                async with runner.stream(RunnerRequest(method="GET", url=_URL)):
                    pass  # pragma: no cover - the stream never opens
            assert status_for_broker_error(excinfo.value) == 502
        with pytest.raises(CircuitOpenError):
            async with runner.stream(RunnerRequest(method="GET", url=_URL)):
                pass  # pragma: no cover - the circuit is open
