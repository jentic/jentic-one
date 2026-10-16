"""Unit tests for the ``DeadlineRunner`` decorator + ``build_runner`` (§09 E4.1 / §11).

The deadline is the always-on whole-call wall-clock budget wrapping any
``UpstreamRunner``: a call that overruns raises ``DeadlineExceededError`` (504)
with a ``wait`` directive; a call within budget passes through verbatim; a
non-positive deadline disables the budget (unbounded passthrough).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from unittest import mock

import httpx
import pytest

from jentic_one.broker.adapters.runners.base import (
    RunnerRequest,
    RunnerResult,
    StreamingResult,
)
from jentic_one.broker.adapters.runners.deadline import DeadlineRunner
from jentic_one.broker.adapters.runners.dispatch import track_dispatch
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.adapters.runners.retry import RetryRunner
from jentic_one.broker.core.exceptions import (
    DeadlineExceededError,
    ErrorOrigin,
    UpstreamTransportError,
    upstream_contact_ruled_out,
)
from jentic_one.broker.services.execution.pipeline import build_runner


class _SlowRunner:
    """Inner runner that sleeps ``delay_s`` before returning a fixed result."""

    def __init__(self, *, delay_s: float = 0.0, status: int = 200) -> None:
        self.delay_s = delay_s
        self.status = status
        self.calls = 0

    async def run(self, request: RunnerRequest) -> RunnerResult:
        self.calls += 1
        await asyncio.sleep(self.delay_s)
        return RunnerResult(
            status_code=self.status, body=b"ok", headers={}, content_type=None, duration_ms=1
        )

    @contextlib.asynccontextmanager
    async def stream(self, request: RunnerRequest) -> AsyncIterator[StreamingResult]:
        self.calls += 1
        await asyncio.sleep(self.delay_s)
        yield StreamingResult(
            status_code=self.status,
            headers={},
            content_type=None,
            aiter=_empty_aiter(),
        )


async def _empty_aiter() -> AsyncIterator[bytes]:
    return
    yield  # pragma: no cover - makes this an async generator


def _request(url: str = "https://api.example.com/v1/x") -> RunnerRequest:
    return RunnerRequest(method="GET", url=url)


@pytest.mark.asyncio
async def test_within_deadline_passes_through() -> None:
    inner = _SlowRunner(delay_s=0.0, status=201)
    runner = DeadlineRunner(inner, deadline_s=1.0)

    result = await runner.run(_request())

    assert result.status_code == 201
    assert result.body == b"ok"
    assert inner.calls == 1


@pytest.mark.asyncio
async def test_exceeding_deadline_raises_504_with_directive() -> None:
    inner = _SlowRunner(delay_s=1.0)
    runner = DeadlineRunner(inner, deadline_s=0.01)

    with pytest.raises(DeadlineExceededError) as exc:
        await runner.run(_request())

    err = exc.value
    assert err.type == "deadline_exceeded"
    # The broker cut the call off and generated the 504 (#1675).
    assert err.origin is ErrorOrigin.BROKER
    assert err.directive is not None
    assert err.directive.strategy == "wait"
    assert err.directive.parameters["deadline_seconds"] == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_non_positive_deadline_disables_budget() -> None:
    inner = _SlowRunner(delay_s=0.05)
    runner = DeadlineRunner(inner, deadline_s=0.0)

    # Would blow a tiny budget, but the deadline is disabled, so it completes.
    result = await runner.run(_request())
    assert result.status_code == 200


@pytest.mark.asyncio
async def test_stream_within_deadline_yields_result() -> None:
    inner = _SlowRunner(delay_s=0.0)
    runner = DeadlineRunner(inner, deadline_s=1.0)

    async with runner.stream(_request()) as result:
        assert result.status_code == 200


@pytest.mark.asyncio
async def test_stream_exceeding_deadline_raises() -> None:
    inner = _SlowRunner(delay_s=1.0)
    runner = DeadlineRunner(inner, deadline_s=0.01)

    with pytest.raises(DeadlineExceededError):
        async with runner.stream(_request()):
            pass  # pragma: no cover - never reached


def test_build_runner_wraps_with_deadline() -> None:
    inner = _SlowRunner()
    composed = build_runner(inner, deadline_s=5.0)

    assert isinstance(composed, DeadlineRunner)


@pytest.mark.asyncio
async def test_build_runner_enforces_deadline() -> None:
    inner = _SlowRunner(delay_s=1.0)
    composed = build_runner(inner, deadline_s=0.01)

    with pytest.raises(DeadlineExceededError):
        await composed.run(_request())


# --- whether a blown deadline can have reached the upstream (#1710) -------------


def _post(url: str = "https://api.example.com/v1/charges") -> RunnerRequest:
    return RunnerRequest(method="POST", url=url, body=b"{}", has_idempotency_key=True)


def _connect_refused(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


@pytest.mark.asyncio
async def test_deadline_during_backoff_after_connect_failures_is_pre_send() -> None:
    """Every attempt failed to connect, then the budget ran out in backoff: nothing was sent."""
    async with httpx.AsyncClient(transport=httpx.MockTransport(_connect_refused)) as client:
        runner = DeadlineRunner(
            RetryRunner(HttpRunner(client), max_attempts=5, base_backoff_s=10, max_backoff_s=10),
            deadline_s=0.05,
        )
        # Full jitter could draw a near-zero sleep; pin it so the deadline fires in backoff.
        with (
            mock.patch("random.uniform", return_value=10.0),
            pytest.raises(DeadlineExceededError) as caught,
        ):
            await runner.run(_post())

    assert caught.value.pre_send is True
    assert upstream_contact_ruled_out(caught.value)


@pytest.mark.asyncio
async def test_deadline_while_queued_for_a_host_permit_is_pre_send() -> None:
    """The budget ran out while the call waited for a per-host bulkhead permit."""
    release = asyncio.Event()

    async def _hang(_request: httpx.Request) -> httpx.Response:
        await release.wait()
        return httpx.Response(201, stream=httpx.ByteStream(b"ok"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(_hang)) as client:
        http = HttpRunner(client, max_per_host=1)
        occupant = asyncio.create_task(http.run(_post()))
        await asyncio.sleep(0.01)
        runner = DeadlineRunner(http, deadline_s=0.05)
        try:
            with pytest.raises(DeadlineExceededError) as caught:
                await runner.run(_post())
        finally:
            release.set()
            await occupant

    assert caught.value.pre_send is True
    assert upstream_contact_ruled_out(caught.value)


@pytest.mark.asyncio
async def test_deadline_cutting_off_a_send_is_not_pre_send() -> None:
    """A send in progress when the budget ran out may have reached the upstream."""

    async def _hang(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(10)
        return httpx.Response(201)  # pragma: no cover - cancelled by the deadline

    async with httpx.AsyncClient(transport=httpx.MockTransport(_hang)) as client:
        runner = DeadlineRunner(HttpRunner(client), deadline_s=0.05)
        with pytest.raises(DeadlineExceededError) as caught:
            await runner.run(_post())

    assert caught.value.pre_send is False
    assert not upstream_contact_ruled_out(caught.value)


@pytest.mark.asyncio
async def test_deadline_after_a_read_timeout_attempt_is_not_pre_send() -> None:
    """An earlier attempt reached the upstream, so a later backoff deadline is not pre-send."""
    calls = 0

    def _read_then_refuse(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("timed out", request=request)
        raise httpx.ConnectError("connection refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_read_then_refuse)) as client:
        runner = DeadlineRunner(
            RetryRunner(HttpRunner(client), max_attempts=5, base_backoff_s=0.0, max_backoff_s=0.0),
            deadline_s=0.05,
        )
        with (
            mock.patch("random.uniform", side_effect=[0.0, 10.0, 10.0, 10.0]),
            pytest.raises(DeadlineExceededError) as caught,
        ):
            await runner.run(_post())

    assert calls == 2
    assert caught.value.pre_send is False
    assert not upstream_contact_ruled_out(caught.value)


class _BodyRaises(httpx.AsyncByteStream):
    """A response body that fails with a connect-class error after the upstream answered."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"partial"
        raise httpx.ConnectError("connection reset")


@pytest.mark.asyncio
async def test_deadline_after_a_connect_error_mid_body_is_not_pre_send() -> None:
    """A send whose body read failed reached the upstream; a later backoff deadline is stored."""
    calls = 0

    def _answer_then_refuse(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, stream=_BodyRaises())
        raise httpx.ConnectError("connection refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(_answer_then_refuse)) as client:
        runner = DeadlineRunner(
            RetryRunner(HttpRunner(client), max_attempts=5, base_backoff_s=0.0, max_backoff_s=0.0),
            deadline_s=0.05,
        )
        with (
            mock.patch("random.uniform", side_effect=[0.0, 10.0, 10.0, 10.0]),
            pytest.raises(DeadlineExceededError) as caught,
        ):
            await runner.run(_post())

    assert calls == 2
    assert caught.value.pre_send is False
    assert not upstream_contact_ruled_out(caught.value)


@pytest.mark.asyncio
async def test_a_ruled_out_send_does_not_hide_a_concurrent_send() -> None:
    """Two sends share one call's probe: ruling one out leaves the in-flight one counted."""
    refuse = asyncio.Event()
    release = asyncio.Event()

    async def _handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/refused":
            await refuse.wait()
            raise httpx.ConnectError("connection refused", request=request)
        await release.wait()
        return httpx.Response(201, stream=httpx.ByteStream(b"ok"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handle)) as client:
        http = HttpRunner(client)
        with track_dispatch() as probe:
            # The refused send starts first, so it is ruled out while the other is in flight.
            refused = asyncio.create_task(http.run(_post("https://a.example.com/refused")))
            await asyncio.sleep(0)
            in_flight = asyncio.create_task(http.run(_post("https://b.example.com/ok")))
            await asyncio.sleep(0.01)
            refuse.set()
            with pytest.raises(UpstreamTransportError):
                await refused
            try:
                assert probe.nothing_sent() is False
            finally:
                release.set()
                await in_flight
        assert probe.nothing_sent() is False


@pytest.mark.asyncio
async def test_deadline_without_a_reporting_transport_is_not_pre_send() -> None:
    """A transport that never reports its sends can't prove nothing was sent."""
    runner = DeadlineRunner(_SlowRunner(delay_s=1.0), deadline_s=0.01)

    with pytest.raises(DeadlineExceededError) as caught:
        await runner.run(_request())

    assert caught.value.pre_send is False
