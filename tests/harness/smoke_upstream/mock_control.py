"""X-Mock-* fault-injection middleware for the smoke upstream harness.

The test runner sends ``X-Mock-*`` headers straight through the broker, which
relays them verbatim to this upstream. The middleware interprets them *before*
the route runs so that resilience behaviour (timeouts, retries, circuit
breaking, transport failures) can be driven deterministically from a test.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Final

import httpx
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

HEADER_STATUS: Final = "X-Mock-Status"
HEADER_DELAY: Final = "X-Mock-Delay"
HEADER_STATUS_SEQUENCE: Final = "X-Mock-Status-Sequence"
HEADER_TEST_ID: Final = "X-Mock-Test-Id"
HEADER_DISCONNECT: Final = "X-Mock-Disconnect"

_DISCONNECT_TRUTHY: Final = "true"
_DISCONNECT_PREFIX: Final = b"partial-body-before-disconnect:"

# Maps an X-Mock-Test-Id to the next index to consume from its status sequence.
# Module-level so state survives across requests within a single test process;
# tests clear it via ``reset_sequences()``. Bounded LRU: load runs (tests/perf)
# mint a fresh test id per logical request, so an unbounded dict would grow for
# the life of the process. Evicting the least-recently-used cursor only resets
# an id nobody has touched in the last ``_MAX_SEQUENCE_CURSORS`` requests.
_MAX_SEQUENCE_CURSORS: Final = 10_000
_sequence_cursors: OrderedDict[str, int] = OrderedDict()


class MockDisconnectError(httpx.RemoteProtocolError):
    """Raised mid-stream to simulate an upstream transport failure.

    Subclasses ``httpx.RemoteProtocolError`` so that under ``httpx.ASGITransport``
    (Mode 1, in-memory) the client observes the same remote-protocol error type
    it would see from a real mid-response TCP sever in Mode 2 (networked
    container). A true socket reset is a deferred Mode-2 concern; here we make
    the in-memory client surface the faithful error type by raising it from the
    streaming body generator.
    """


def reset_sequences() -> None:
    """Clear all per-test-id status-sequence cursors."""
    _sequence_cursors.clear()


def _bad_request(detail: str) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": "invalid_mock_header", "detail": detail})


def _parse_status(raw: str) -> int | None:
    """Parse a status-code header value, or ``None`` if malformed."""
    try:
        code = int(raw)
    except ValueError:
        return None
    if not 100 <= code <= 599:
        return None
    return code


def _parse_sequence(raw: str) -> list[int] | None:
    """Parse a comma-separated status sequence, or ``None`` if malformed."""
    parts = [segment.strip() for segment in raw.split(",") if segment.strip()]
    if not parts:
        return None
    codes: list[int] = []
    for part in parts:
        code = _parse_status(part)
        if code is None:
            return None
        codes.append(code)
    return codes


def _next_sequence_status(test_id: str, sequence: list[int]) -> int:
    """Return the next status for *test_id*, clamping to the last once exhausted."""
    index = _sequence_cursors.get(test_id, 0)
    clamped = min(index, len(sequence) - 1)
    _sequence_cursors[test_id] = index + 1
    _sequence_cursors.move_to_end(test_id)
    while len(_sequence_cursors) > _MAX_SEQUENCE_CURSORS:
        _sequence_cursors.popitem(last=False)
    return sequence[clamped]


async def _disconnecting_stream() -> AsyncIterator[bytes]:
    """Yield a partial body then raise to abort the response mid-stream."""
    yield _DISCONNECT_PREFIX
    raise MockDisconnectError("simulated mid-response disconnect")


class MockControlMiddleware(BaseHTTPMiddleware):
    """Intercepts ``X-Mock-*`` headers to inject deterministic faults."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        headers = request.headers

        sequence_raw = headers.get(HEADER_STATUS_SEQUENCE)
        if sequence_raw is not None:
            test_id = headers.get(HEADER_TEST_ID)
            if not test_id:
                return _bad_request(f"{HEADER_STATUS_SEQUENCE} requires {HEADER_TEST_ID}")
            sequence = _parse_sequence(sequence_raw)
            if sequence is None:
                return _bad_request(f"malformed {HEADER_STATUS_SEQUENCE}: {sequence_raw!r}")
            status = _next_sequence_status(test_id, sequence)
            return JSONResponse(status_code=status, content={"mock": "status-sequence"})

        status_raw = headers.get(HEADER_STATUS)
        if status_raw is not None:
            status = _parse_status(status_raw) or 0
            if status == 0:
                return _bad_request(f"malformed {HEADER_STATUS}: {status_raw!r}")
            return JSONResponse(status_code=status, content={"mock": "status"})

        if headers.get(HEADER_DISCONNECT, "").lower() == _DISCONNECT_TRUTHY:
            return StreamingResponse(_disconnecting_stream(), media_type="application/octet-stream")

        delay_raw = headers.get(HEADER_DELAY)
        if delay_raw is not None:
            try:
                delay = float(delay_raw)
            except ValueError:
                return _bad_request(f"malformed {HEADER_DELAY}: {delay_raw!r}")
            if delay < 0:
                return _bad_request(f"{HEADER_DELAY} must be non-negative: {delay_raw!r}")
            await asyncio.sleep(delay)

        return await call_next(request)
