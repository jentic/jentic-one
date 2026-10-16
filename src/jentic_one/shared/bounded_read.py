"""Bounded reads of streamed upstream HTTP responses.

Upstream spec/manifest fetches must not buffer an arbitrarily large body before
the size cap applies. ``httpx``'s non-streaming ``client.get`` reads the whole
body into ``resp.content`` first, so a chunked response (no ``Content-Length``)
is fully materialized before any ``len()`` check. Callers instead open the
response with ``client.send(request, stream=True)`` and read it through
:func:`read_capped_body`, which rejects an oversized ``Content-Length`` up front
and otherwise counts bytes as they arrive, aborting as soon as the running total
crosses the cap — at most one chunk past the limit is ever held in memory.
"""

from __future__ import annotations

import httpx


class ResponseTooLargeError(Exception):
    """Raised when an upstream response body exceeds the configured byte cap."""


async def open_stream(
    client: httpx.AsyncClient, url: str, *, headers: dict[str, str] | None = None
) -> httpx.Response:
    """Send a ``GET`` and return the response with its body still unread.

    The caller owns the response and must ``aclose()`` it (redirect hops
    included) — :func:`read_capped_body` does not close it.
    """
    request = client.build_request("GET", url, headers=headers)
    return await client.send(request, stream=True)


async def read_capped_body(resp: httpx.Response, max_bytes: int) -> bytes:
    """Read a streamed response body, raising ``ResponseTooLargeError`` past ``max_bytes``.

    A well-formed ``Content-Length`` above the cap is rejected before any body
    byte is read; a missing or malformed one falls through to the running count.
    The cap applies to the decoded body (``aiter_bytes`` undoes any
    ``Content-Encoding``), matching what ``resp.content`` would have held.
    """
    content_length = resp.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > max_bytes:
        raise ResponseTooLargeError
    chunks: list[bytes] = []
    total = 0
    async for chunk in resp.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise ResponseTooLargeError
        chunks.append(chunk)
    return b"".join(chunks)


def decode_body(resp: httpx.Response, body: bytes) -> str:
    """Decode a body read via :func:`read_capped_body` exactly as ``resp.text`` would.

    ``resp.text`` is unavailable on a streamed response read through
    ``aiter_bytes``; this applies the same charset resolution (``Content-Type``
    charset, else UTF-8) and ``errors="replace"`` policy.
    """
    return body.decode(resp.encoding or "utf-8", errors="replace")
