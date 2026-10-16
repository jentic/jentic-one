"""Tests for the streamed, byte-capped upstream body read (``shared.bounded_read``)."""

import pytest

from jentic_one.shared.bounded_read import (
    ResponseTooLargeError,
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
