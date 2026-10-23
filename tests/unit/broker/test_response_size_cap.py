"""Unit tests for the response-size cap enforced mid-stream by the HTTP runner (§08 E2.4).

A body over ``max_response_bytes`` aborts **mid-stream** with
``UpstreamResponseTooLargeError`` (502) — before the whole oversized body is
buffered — so a hostile/large upstream can't OOM the instance. A body at/under
the cap passes verbatim, and a cap of 0 disables the check.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from jentic_one.broker.adapters.runners.base import RunnerRequest
from jentic_one.broker.adapters.runners.http import HttpRunner
from jentic_one.broker.core.exceptions import UpstreamResponseTooLargeError
from jentic_one.shared.broker.execution import ErrorOrigin


class _RecordingStream:
    """An async byte stream that records how many chunks were actually pulled."""

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
async def test_body_under_cap_passes_verbatim() -> None:
    stream = _RecordingStream([b"abc", b"def"])
    async with _streaming_client(stream) as client:
        runner = HttpRunner(client, max_response_bytes=64)
        result = await runner.run(RunnerRequest(method="GET", url="https://api.example.com/x"))
    assert result.body == b"abcdef"
    assert result.status_code == 200


@pytest.mark.asyncio
async def test_body_at_cap_exact_passes() -> None:
    stream = _RecordingStream([b"abcd", b"efgh"])  # exactly 8 bytes
    async with _streaming_client(stream) as client:
        runner = HttpRunner(client, max_response_bytes=8)
        result = await runner.run(RunnerRequest(method="GET", url="https://api.example.com/x"))
    assert result.body == b"abcdefgh"


@pytest.mark.asyncio
async def test_body_over_cap_aborts_mid_stream() -> None:
    # 5 chunks of 4 bytes = 20 bytes; cap is 8, so we should abort after the
    # third chunk pushes the running total to 12 (> 8) — never reading chunks 4-5.
    stream = _RecordingStream([b"aaaa", b"bbbb", b"cccc", b"dddd", b"eeee"])
    async with _streaming_client(stream) as client:
        runner = HttpRunner(client, max_response_bytes=8)
        with pytest.raises(UpstreamResponseTooLargeError) as exc_info:
            await runner.run(RunnerRequest(method="GET", url="https://api.example.com/x"))

    err = exc_info.value
    # The broker aborted the vendor's body and generated the 502 (#1675).
    assert err.origin is ErrorOrigin.BROKER
    assert err.type == "upstream_response_too_large"
    assert err.extra == {"max_response_bytes": 8}
    assert err.directive is not None and err.directive.strategy == "switch_toolkit"
    # Mid-stream abort: we stopped as soon as the cap was breached, not after a
    # full read of all five chunks.
    assert stream.consumed < 5


@pytest.mark.asyncio
async def test_cap_zero_disables_check() -> None:
    stream = _RecordingStream([b"x" * 1000])
    async with _streaming_client(stream) as client:
        runner = HttpRunner(client, max_response_bytes=0)
        result = await runner.run(RunnerRequest(method="GET", url="https://api.example.com/x"))
    assert len(result.body) == 1000
