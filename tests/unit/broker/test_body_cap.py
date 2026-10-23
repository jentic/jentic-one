"""Unit tests for the broker capped body read (§04).

A body over the resolved cap fails **mid-stream** with ``PayloadTooLargeError``
(413 centrally) — it does not read the whole body first. Chunked uploads (no
``Content-Length``) are assembled and capped the same way (never 411).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from jentic_one.broker.core.errors import PayloadTooLargeError
from jentic_one.broker.web.routers.execute import _read_capped_body


class _FakeRequest:
    """Minimal stand-in exposing only ``stream()`` (what _read_capped_body uses).

    Tracks how many chunks were yielded so a test can assert the read stops
    *mid-stream* rather than draining the whole body before raising.
    """

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.yielded = 0

    async def stream(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            self.yielded += 1
            yield chunk


@pytest.mark.asyncio
async def test_under_cap_assembles_full_body() -> None:
    req = _FakeRequest([b"abc", b"def", b"gh"])
    body = await _read_capped_body(req, max_bytes=100)  # type: ignore[arg-type]
    assert body == b"abcdefgh"
    assert req.yielded == 3


@pytest.mark.asyncio
async def test_over_cap_raises_413_mid_stream() -> None:
    # 5 chunks of 4 bytes = 20 bytes total, cap 10 → must trip on the 3rd chunk
    # (12 > 10) and never pull the 4th/5th.
    req = _FakeRequest([b"aaaa", b"bbbb", b"cccc", b"dddd", b"eeee"])
    with pytest.raises(PayloadTooLargeError):
        await _read_capped_body(req, max_bytes=10)  # type: ignore[arg-type]
    assert req.yielded == 3


@pytest.mark.asyncio
async def test_chunked_upload_capped_same_way() -> None:
    # No Content-Length is involved — stream() yields de-chunked bytes; over-cap
    # still raises 413 (never 411 Length Required).
    req = _FakeRequest([b"x" * 8, b"y" * 8])
    with pytest.raises(PayloadTooLargeError):
        await _read_capped_body(req, max_bytes=10)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_exactly_at_cap_is_allowed() -> None:
    req = _FakeRequest([b"x" * 10])
    body = await _read_capped_body(req, max_bytes=10)  # type: ignore[arg-type]
    assert len(body) == 10
