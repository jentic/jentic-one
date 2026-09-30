"""Unit tests for the streaming-passthrough path (§08 E2.4).

Covers the runner's ``stream()`` (verbatim body, mid-stream size-cap abort with
upstream teardown) and the web edge's ``guarded_body`` / ``open_streaming_response``
(transfer-deadline abort, client-disconnect teardown — no zombie upstream stream).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
import pytest

from jentic_one.broker.adapters.runners.base import RunnerRequest, RunnerResult, StreamingResult
from jentic_one.broker.adapters.runners.circuit import CircuitBreakerRunner
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.core.exceptions import (
    CircuitOpenError,
    UpstreamResponseTooLargeError,
    UpstreamTimeoutError,
)
from jentic_one.broker.core.headers import JenticHeader
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.web.streaming import guarded_body, open_streaming_response
from jentic_one.shared.resilience import CircuitBreaker
from jentic_one.shared.resilience.circuit import _FAIL_PREFIX
from jentic_one.shared.state.backend import MemoryStateBackend


class _RecordingStream:
    """An async byte stream recording how many chunks were pulled."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.consumed = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            self.consumed += 1
            yield chunk


def _streaming_client(stream: _RecordingStream, *, status: int = 200) -> httpx.AsyncClient:
    def handle(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=stream.__aiter__())

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


@pytest.mark.asyncio
async def test_stream_yields_body_verbatim() -> None:
    stream = _RecordingStream([b"hello, ", b"world"])
    async with _streaming_client(stream) as client:
        runner = HttpRunner(client, max_response_bytes=64)
        async with runner.stream(
            RunnerRequest(method="GET", url="https://api.example.com/x")
        ) as result:
            assert result.status_code == 200
            collected = b"".join([chunk async for chunk in result.aiter])
    assert collected == b"hello, world"


@pytest.mark.asyncio
async def test_stream_size_cap_aborts_and_tears_down_upstream() -> None:
    stream = _RecordingStream([b"aaaa", b"bbbb", b"cccc", b"dddd"])  # 16 bytes; cap 8
    async with _streaming_client(stream) as client:
        runner = HttpRunner(client, max_response_bytes=8)
        with pytest.raises(UpstreamResponseTooLargeError):
            async with runner.stream(
                RunnerRequest(method="GET", url="https://api.example.com/x")
            ) as result:
                async for _ in result.aiter:
                    pass
    # Aborted mid-stream — not all four chunks were read before the cap fired.
    # (Upstream teardown is owned by httpx's ``client.stream`` context manager,
    # which the runner exits on the raise; the edge tests assert the
    # context-unwinding teardown contract directly via a fake stream context.)
    assert stream.consumed < 4


# --- web-edge guards (guarded_body / open_streaming_response) ------------------


class _FakeStreamCtx:
    """A fake runner.stream() context recording aclose() of the upstream."""

    def __init__(self, result: StreamingResult) -> None:
        self._result = result
        self.closed = False

    @asynccontextmanager
    async def open(self) -> AsyncIterator[StreamingResult]:
        try:
            yield self._result
        finally:
            self.closed = True


async def _slow_iter(chunks: list[bytes], delay_s: float) -> AsyncIterator[bytes]:
    for chunk in chunks:
        await asyncio.sleep(delay_s)
        yield chunk


@pytest.mark.asyncio
async def test_guarded_body_transfer_deadline_aborts() -> None:
    result = StreamingResult(
        status_code=200,
        headers={},
        content_type="text/plain",
        aiter=_slow_iter([b"a", b"b", b"c"], delay_s=0.05),
    )
    fake = _FakeStreamCtx(result)
    stack = AsyncExitStack()
    await stack.enter_async_context(fake.open())

    body = guarded_body(result, stack, transfer_deadline_s=0.06)
    with pytest.raises(UpstreamTimeoutError):
        async for _ in body:
            pass
    # The deadline fired; the stack unwound and closed the upstream stream.
    assert fake.closed is True


@pytest.mark.asyncio
async def test_guarded_body_client_disconnect_tears_down_upstream() -> None:
    closed_marker = {"closed": False}

    async def _endless() -> AsyncIterator[bytes]:
        try:
            while True:
                yield b"data"
                await asyncio.sleep(0.01)
        finally:
            closed_marker["closed"] = True

    result = StreamingResult(status_code=200, headers={}, content_type=None, aiter=_endless())
    fake = _FakeStreamCtx(result)
    stack = AsyncExitStack()
    await stack.enter_async_context(fake.open())

    body = guarded_body(result, stack, transfer_deadline_s=0)

    # Simulate Starlette consuming a couple of chunks then the client disconnecting
    # (the generator is aclose()d, raising GeneratorExit inside it).
    agen: AsyncGenerator[bytes, None] = body
    assert await agen.__anext__() == b"data"
    assert await agen.__anext__() == b"data"
    await agen.aclose()

    # The body generator's finally ran: the AsyncExitStack closed the upstream.
    assert fake.closed is True


@pytest.mark.asyncio
async def test_open_streaming_response_sets_metadata_headers() -> None:
    result = StreamingResult(
        status_code=503,
        headers={"content-type": "application/json", "x-vendor": "v"},
        content_type="application/json",
        aiter=_slow_iter([b"{}"], delay_s=0.0),
    )
    fake = _FakeStreamCtx(result)

    class _Runner:
        def stream(self, _req: RunnerRequest) -> object:
            return fake.open()

    ctx_req = ExecuteRequestContext(
        upstream_url="https://api.example.com/x",
        method="GET",
        trace_id="t",
        operation_id="op",
        api_vendor="vendor",
    )
    resp = await open_streaming_response(
        _Runner(),  # type: ignore[arg-type]
        RunnerRequest(method="GET", url="https://api.example.com/x"),
        ctx_req,
        "exec-1",
        transfer_deadline_s=0,
    )

    assert resp.status_code == 503
    assert resp.headers[JenticHeader.EXECUTION_ID.value] == "exec-1"
    assert resp.headers[JenticHeader.UPSTREAM_STATUS.value] == "503"
    assert resp.headers[JenticHeader.ERROR_ORIGIN.value] == "upstream"
    # x-vendor passed through; content-length not present (chunked transfer).
    assert resp.headers["x-vendor"] == "v"


# --- circuit-breaker streaming decorator --------------------------------------


class _FakeStreamingInner:
    """Inner streaming runner returning a fixed status, counting stream() calls."""

    def __init__(self, *, status: int = 200) -> None:
        self.status = status
        self.calls = 0

    async def run(self, request: RunnerRequest) -> RunnerResult:
        self.calls += 1
        return RunnerResult(
            status_code=self.status, body=b"", headers={}, content_type=None, duration_ms=1
        )

    @asynccontextmanager
    async def stream(self, _req: RunnerRequest) -> AsyncIterator[StreamingResult]:
        self.calls += 1
        yield StreamingResult(
            status_code=self.status,
            headers={},
            content_type=None,
            aiter=_slow_iter([b"ok"], delay_s=0.0),
        )


@pytest.mark.asyncio
async def test_circuit_stream_open_blocks() -> None:
    backend = MemoryStateBackend()
    breaker = CircuitBreaker(backend, failure_ratio=0.5, min_calls=2, window_s=30, cooldown_s=15)
    inner = _FakeStreamingInner(status=500)
    runner = CircuitBreakerRunner(inner, breaker, enforcement_mode="blocking")

    # Two 500s trip the circuit (ratio 1.0, min_calls 2).
    await runner.run(RunnerRequest(method="GET", url="https://api.example.com/x"))
    await runner.run(RunnerRequest(method="GET", url="https://api.example.com/x"))
    calls_before = inner.calls

    with pytest.raises(CircuitOpenError):
        async with runner.stream(RunnerRequest(method="GET", url="https://api.example.com/x")):
            pass
    # The shed stream request never reached the inner runner.
    assert inner.calls == calls_before


@pytest.mark.asyncio
async def test_circuit_stream_records_success_on_headers() -> None:
    backend = MemoryStateBackend()
    breaker = CircuitBreaker(backend, failure_ratio=0.5, min_calls=2, window_s=30, cooldown_s=15)
    inner = _FakeStreamingInner(status=200)
    runner = CircuitBreakerRunner(inner, breaker, enforcement_mode="blocking")

    async with runner.stream(RunnerRequest(method="GET", url="https://api.example.com/x")) as r:
        assert r.status_code == 200
        body = b"".join([chunk async for chunk in r.aiter])
    assert body == b"ok"
    assert inner.calls == 1


class _FakeStreamingInnerRaisesAfterHeader:
    """Streaming runner that yields a 5xx header then raises BrokerError mid-stream."""

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, request: RunnerRequest) -> RunnerResult:
        raise NotImplementedError

    @asynccontextmanager
    async def stream(self, _req: RunnerRequest) -> AsyncIterator[StreamingResult]:
        self.calls += 1

        async def _exploding_iter() -> AsyncIterator[bytes]:
            yield b"partial"
            raise UpstreamResponseTooLargeError(detail="boom")

        yield StreamingResult(
            status_code=502,
            headers={},
            content_type=None,
            aiter=_exploding_iter(),
        )


@pytest.mark.asyncio
async def test_circuit_stream_no_double_record_on_midstream_error() -> None:
    """A BrokerError after headers were already recorded must not count twice."""
    backend = MemoryStateBackend()
    breaker = CircuitBreaker(backend, failure_ratio=0.5, min_calls=2, window_s=30, cooldown_s=15)
    inner = _FakeStreamingInnerRaisesAfterHeader()
    runner = CircuitBreakerRunner(inner, breaker, enforcement_mode="blocking")

    with pytest.raises(UpstreamResponseTooLargeError):
        async with runner.stream(RunnerRequest(method="GET", url="https://api.example.com/x")) as r:
            async for _ in r.aiter:
                pass

    # Only one failure recorded — the header-based record, NOT a second from the
    # except-BrokerError clause (the `recorded` guard prevents double-counting).
    fail_count = await backend.get(f"{_FAIL_PREFIX}api.example.com")
    assert fail_count == b"1"
