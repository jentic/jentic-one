"""Tests for the streamed, byte-capped upstream body read (``shared.bounded_read``)."""

import gzip
import itertools
import tracemalloc
import zlib
from collections.abc import AsyncIterator, Callable, Iterable

import httpx
import pytest

from jentic_one.shared import bounded_read
from jentic_one.shared.bounded_read import (
    ResponseTooLargeError,
    UndecodableBodyError,
    decode_body,
    open_stream,
    read_capped_body,
)
from tests.harness.http_doubles import CHUNK_SIZE, CountingChunkedStream, chunked_client

_URL = "https://example.com/spec.json"


@pytest.mark.asyncio
async def test_chunked_body_over_cap_aborts_one_chunk_past_limit() -> None:
    """No Content-Length: the read stops at the first chunk that crosses the cap."""
    stream = CountingChunkedStream(n_chunks=1000)
    async with chunked_client(stream) as client:
        resp = await open_stream(client, _URL)
        assert "content-length" not in resp.headers
        with pytest.raises(ResponseTooLargeError):
            await read_capped_body(resp, max_bytes=4 * CHUNK_SIZE)
        await resp.aclose()
    # The cap is exactly 4 chunks; the 5th crosses it and nothing further is pulled.
    assert stream.served == 5
    assert stream.closed


@pytest.mark.asyncio
async def test_chunked_body_at_cap_is_returned_whole() -> None:
    stream = CountingChunkedStream(n_chunks=4)
    async with chunked_client(stream) as client:
        resp = await open_stream(client, _URL)
        body = await read_capped_body(resp, max_bytes=4 * CHUNK_SIZE)
        await resp.aclose()
    assert body == b"x" * (4 * CHUNK_SIZE)
    assert stream.served == 4


@pytest.mark.asyncio
async def test_oversized_content_length_rejected_before_reading_body() -> None:
    stream = CountingChunkedStream(n_chunks=10)
    headers = {"content-length": str(10 * CHUNK_SIZE)}
    async with chunked_client(stream, headers=headers) as client:
        resp = await open_stream(client, _URL)
        with pytest.raises(ResponseTooLargeError):
            await read_capped_body(resp, max_bytes=4 * CHUNK_SIZE)
        await resp.aclose()
    assert stream.served == 0


@pytest.mark.asyncio
async def test_malformed_content_length_falls_through_to_running_count() -> None:
    stream = CountingChunkedStream(n_chunks=2)
    async with chunked_client(stream, headers={"content-length": "abc"}) as client:
        resp = await open_stream(client, _URL)
        body = await read_capped_body(resp, max_bytes=4 * CHUNK_SIZE)
        await resp.aclose()
    assert len(body) == 2 * CHUNK_SIZE


@pytest.mark.asyncio
async def test_decode_body_honours_content_type_charset() -> None:
    stream = CountingChunkedStream(n_chunks=1, chunk="café".encode("latin-1"))
    headers = {"content-type": "application/json; charset=latin-1"}
    async with chunked_client(stream, headers=headers) as client:
        resp = await open_stream(client, _URL)
        body = await read_capped_body(resp, max_bytes=CHUNK_SIZE)
        await resp.aclose()
    assert decode_body(resp, body) == "café"


class _RawChunks(httpx.AsyncByteStream):
    """Serve ``data`` in fixed-size raw chunks (already-encoded wire bytes)."""

    def __init__(self, data: bytes, chunk_size: int = 64 * 1024) -> None:
        self.data = data
        self.chunk_size = chunk_size

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for start in range(0, len(self.data), self.chunk_size):
            yield self.data[start : start + self.chunk_size]


def _encoded_client(
    data: bytes, encoding: str, seen: list[httpx.Request] | None = None
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, stream=_RawChunks(data), headers={"content-encoding": encoding})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_open_stream_requests_identity_encoding() -> None:
    seen: list[httpx.Request] = []
    async with _encoded_client(b"{}", "identity", seen) as client:
        resp = await open_stream(client, _URL, headers={"user-agent": "ua", "x-extra": "1"})
        await resp.aclose()
    assert seen[0].headers["accept-encoding"] == "identity"
    assert seen[0].headers["user-agent"] == "ua"
    assert seen[0].headers["x-extra"] == "1"


@pytest.mark.asyncio
async def test_gzip_bomb_rejected_with_bounded_decode_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A gzip of 64 MiB of zeros (~64 KB on the wire, one raw chunk) is refused
    without inflating any piece larger than one decode step."""
    cap = 8 * 1024 * 1024
    bomb = gzip.compress(b"\0" * (64 * 1024 * 1024), compresslevel=9)
    pieces: list[int] = []
    original_add = bounded_read._CappedBuffer.add

    def _spy(self: bounded_read._CappedBuffer, piece: bytes) -> None:
        pieces.append(len(piece))
        original_add(self, piece)

    monkeypatch.setattr(bounded_read._CappedBuffer, "add", _spy)
    async with _encoded_client(bomb, "gzip") as client:
        resp = await open_stream(client, _URL)
        with pytest.raises(ResponseTooLargeError):
            await read_capped_body(resp, max_bytes=cap)
        await resp.aclose()
    assert pieces
    assert max(pieces) <= bounded_read.DECODE_STEP_BYTES
    assert sum(pieces) <= cap + bounded_read.DECODE_STEP_BYTES


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("encoding", "encode"),
    [
        ("gzip", gzip.compress),
        ("deflate", zlib.compress),
        ("deflate", lambda b: zlib.compress(b, wbits=-zlib.MAX_WBITS)),
    ],
    ids=["gzip", "deflate-zlib", "deflate-raw"],
)
async def test_compressed_body_under_cap_round_trips(
    encoding: str, encode: Callable[[bytes], bytes]
) -> None:
    payload = b'{"openapi": "3.1.0"}' * 20_000
    async with _encoded_client(encode(payload), encoding) as client:
        resp = await open_stream(client, _URL)
        body = await read_capped_body(resp, max_bytes=len(payload))
        await resp.aclose()
    assert body == payload


@pytest.mark.asyncio
async def test_compressed_body_one_byte_over_cap_rejected() -> None:
    payload = b"a" * 10_000
    async with _encoded_client(gzip.compress(payload), "gzip") as client:
        resp = await open_stream(client, _URL)
        with pytest.raises(ResponseTooLargeError):
            await read_capped_body(resp, max_bytes=len(payload) - 1)
        await resp.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("encoding", "data"),
    [("br", b"\x0b\x02\x80{}\x03"), ("gzip", b"not gzip at all")],
    ids=["unsupported", "corrupt"],
)
async def test_undecodable_body_rejected(encoding: str, data: bytes) -> None:
    async with _encoded_client(data, encoding) as client:
        resp = await open_stream(client, _URL)
        with pytest.raises(UndecodableBodyError):
            await read_capped_body(resp, max_bytes=CHUNK_SIZE)
        await resp.aclose()


class _CountingRawChunks(httpx.AsyncByteStream):
    """Serve pre-encoded raw ``chunks`` in order, counting how many were pulled."""

    def __init__(self, chunks: Iterable[bytes]) -> None:
        self.chunks = chunks
        self.served = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.served += 1
            yield chunk


def _stream_client(stream: httpx.AsyncByteStream, encoding: str) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream, headers={"content-encoding": encoding})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_trailing_data_after_gzip_end_refused_with_bounded_memory() -> None:
    """A tiny gzip followed by 80 MiB of junk (no Content-Length) is refused
    under a 1 MiB cap as soon as the junk arrives: bytes after end-of-stream are
    never buffered, so the stream stops being pulled and memory stays small."""
    junk = b"j" * (64 * 1024)
    stream = _CountingRawChunks(
        itertools.chain([gzip.compress(b"{}")], itertools.repeat(junk, 1280))
    )
    tracemalloc.start()
    try:
        async with _stream_client(stream, "gzip") as client:
            resp = await open_stream(client, _URL)
            with pytest.raises(UndecodableBodyError, match="trailing data"):
                await read_capped_body(resp, max_bytes=1024 * 1024)
            await resp.aclose()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert stream.served == 2
    assert peak < 4 * 1024 * 1024


@pytest.mark.asyncio
async def test_trailing_data_in_same_chunk_as_gzip_end_refused() -> None:
    """End-of-stream and trailing bytes arriving in one raw chunk are refused too."""
    stream = _CountingRawChunks([gzip.compress(b"{}") + b"junk", b"more"])
    async with _stream_client(stream, "gzip") as client:
        resp = await open_stream(client, _URL)
        with pytest.raises(UndecodableBodyError, match="trailing data"):
            await read_capped_body(resp, max_bytes=CHUNK_SIZE)
        await resp.aclose()
    assert stream.served == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["gzip, identity", "identity, gzip", "identity,  GZIP "])
async def test_identity_alongside_one_coding_is_decoded(encoding: str) -> None:
    payload = b'{"openapi": "3.1.0"}'
    async with _encoded_client(gzip.compress(payload), encoding) as client:
        resp = await open_stream(client, _URL)
        body = await read_capped_body(resp, max_bytes=CHUNK_SIZE)
        await resp.aclose()
    assert body == payload


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["gzip, gzip", "deflate, gzip", "gzip, br"])
async def test_stacked_codings_refused(encoding: str) -> None:
    data = gzip.compress(gzip.compress(b"{}"))
    async with _encoded_client(data, encoding) as client:
        resp = await open_stream(client, _URL)
        with pytest.raises(UndecodableBodyError, match="unsupported content-encoding"):
            await read_capped_body(resp, max_bytes=CHUNK_SIZE)
        await resp.aclose()
