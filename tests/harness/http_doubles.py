"""``httpx`` doubles for upstream spec fetches (real clients over ``MockTransport``).

The registry fetchers open upstream responses as streams and read them under a
byte cap, so tests hand them a real ``httpx.AsyncClient`` backed by a
``MockTransport`` rather than an ``AsyncMock`` that only stubs ``client.get``.
``CountingChunkedStream`` serves a body with no ``Content-Length`` and records
how far the reader got before aborting.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

CHUNK_SIZE = 1024


class CountingChunkedStream(httpx.AsyncByteStream):
    """Yield ``n_chunks`` copies of ``chunk``, recording each one served."""

    def __init__(self, n_chunks: int, chunk: bytes = b"x" * CHUNK_SIZE) -> None:
        self.n_chunks = n_chunks
        self.chunk = chunk
        self.served = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(self.n_chunks):
            self.served += 1
            yield self.chunk

    async def aclose(self) -> None:
        self.closed = True


def chunked_client(
    stream: httpx.AsyncByteStream,
    *,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> httpx.AsyncClient:
    """An ``AsyncClient`` whose every request is answered with ``stream`` as the body."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=status_code, stream=stream, headers=headers or {})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def spec_client(spec_text: str) -> httpx.AsyncClient:
    """An ``AsyncClient`` answering every ``GET`` with ``spec_text`` (200, sized body)."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, content=spec_text.encode())

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))
