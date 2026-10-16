"""Bounded reads of streamed upstream HTTP responses.

Upstream spec/manifest fetches must not buffer an arbitrarily large body before
the size cap applies. ``httpx``'s non-streaming ``client.get`` reads the whole
body into ``resp.content`` first, so a chunked response (no ``Content-Length``)
is fully materialized before any ``len()`` check. Callers instead open the
response with :func:`open_stream` (or :func:`follow_redirects`) and read it
through :func:`read_capped_body`, which rejects an oversized ``Content-Length``
up front and otherwise counts decoded bytes as they arrive, aborting as soon as
the running total crosses the cap.

Compressed bodies are decoded here rather than by ``httpx``: its decoders
inflate each network chunk in one unbounded call, so a few KB of gzip can
expand to tens of MB in a single step (a decompression bomb). Requests ask for
``Accept-Encoding: identity``; when an upstream compresses anyway (gzip or
deflate), the raw stream is inflated incrementally with an output bound, so
the memory held is at most ``max_bytes`` plus one :data:`DECODE_STEP_BYTES`
step and one raw network chunk. Any other ``Content-Encoding`` is refused.
"""

from __future__ import annotations

import zlib
from collections.abc import Callable
from urllib.parse import urljoin

import httpx

from jentic_one.shared.config import EgressConfig
from jentic_one.shared.url_validation import validate_upstream_url_async

#: Largest decoded piece produced per inflate step of a compressed body.
DECODE_STEP_BYTES = 64 * 1024


class UpstreamBodyError(Exception):
    """Base for upstream response bodies that cannot be read under the cap."""


class ResponseTooLargeError(UpstreamBodyError):
    """Raised when an upstream response body exceeds the configured byte cap."""


class UndecodableBodyError(UpstreamBodyError):
    """Raised when a body uses an unsupported or corrupt ``Content-Encoding``."""


async def open_stream(
    client: httpx.AsyncClient, url: str, *, headers: dict[str, str] | None = None
) -> httpx.Response:
    """Send a ``GET`` asking for an uncompressed body; return it with the body unread.

    ``Accept-Encoding: identity`` is always sent (caller ``headers`` set every
    other field). The caller owns the response and must ``aclose()`` it
    (redirect hops included) — :func:`read_capped_body` does not close it.
    """
    merged = httpx.Headers(headers or {})
    merged["accept-encoding"] = "identity"
    request = client.build_request("GET", url, headers=merged)
    return await client.send(request, stream=True)


async def follow_redirects(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str] | None,
    egress: EgressConfig,
    max_redirects: int,
    on_unsafe: Callable[[ValueError], Exception],
    on_too_many: Callable[[], Exception],
) -> httpx.Response:
    """Open ``url`` as a stream, following up to ``max_redirects`` revalidated hops.

    Every ``Location`` is resolved against the current URL and re-checked by
    ``validate_upstream_url_async`` against ``egress`` before it is requested;
    ``headers`` are re-sent on each hop. A rejected hop raises
    ``on_unsafe(exc)``, and a redirect still pending once the budget is spent
    raises ``on_too_many()``. Intermediate redirect responses are closed
    unread. The returned final response's body is unread — the caller must
    ``aclose()`` it.
    """
    resp = await open_stream(client, url, headers=headers)
    try:
        for _ in range(max_redirects):
            if resp.status_code < 300 or resp.status_code >= 400:
                break
            location = resp.headers.get("location")
            if not location:
                break
            try:
                url = await validate_upstream_url_async(urljoin(url, location), egress)
            except ValueError as exc:
                raise on_unsafe(exc) from exc
            await resp.aclose()
            resp = await open_stream(client, url, headers=headers)
        else:
            if 300 <= resp.status_code < 400:
                raise on_too_many()
    except BaseException:
        await resp.aclose()
        raise
    return resp


class _CappedBuffer:
    """Accumulates body pieces, raising ``ResponseTooLargeError`` past ``max_bytes``."""

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self.total = 0
        self._chunks: list[bytes] = []

    @property
    def remaining(self) -> int:
        return self.max_bytes - self.total

    def add(self, piece: bytes) -> None:
        self.total += len(piece)
        if self.total > self.max_bytes:
            raise ResponseTooLargeError
        self._chunks.append(piece)

    def value(self) -> bytes:
        return b"".join(self._chunks)


class _Inflater:
    """Incremental gzip/deflate decoder whose every output step is bounded."""

    def __init__(self, encoding: str) -> None:
        # gzip: wbits 16+MAX_WBITS. deflate: try the zlib wrapper first and
        # fall back to a raw stream, as servers disagree on what "deflate" means.
        is_gzip = encoding in ("gzip", "x-gzip")
        wbits = 16 + zlib.MAX_WBITS if is_gzip else zlib.MAX_WBITS
        self._decoder = zlib.decompressobj(wbits=wbits)
        self._raw_fallback = not is_gzip

    def feed(self, data: bytes, out: _CappedBuffer) -> None:
        while data:
            step = min(DECODE_STEP_BYTES, out.remaining + 1)
            try:
                piece = self._decoder.decompress(data, step)
            except zlib.error as exc:
                if not self._raw_fallback:
                    raise UndecodableBodyError(f"corrupt compressed body: {exc}") from exc
                self._decoder = zlib.decompressobj(wbits=-zlib.MAX_WBITS)
                self._raw_fallback = False
                continue
            self._raw_fallback = False
            out.add(piece)
            data = self._decoder.unconsumed_tail
            if not piece and data:
                raise UndecodableBodyError("compressed body makes no progress")
        # Output can still be pending inside zlib after the input is consumed.
        while not self._decoder.eof:
            piece = self._decoder.decompress(b"", min(DECODE_STEP_BYTES, out.remaining + 1))
            if not piece:
                break
            out.add(piece)

    def finish(self, out: _CappedBuffer) -> None:
        try:
            out.add(self._decoder.flush())
        except zlib.error as exc:
            raise UndecodableBodyError(f"corrupt compressed body: {exc}") from exc


def _inflater_for(resp: httpx.Response) -> _Inflater | None:
    encoding = resp.headers.get("content-encoding", "").strip().lower()
    if encoding in ("", "identity"):
        return None
    if encoding in ("gzip", "x-gzip", "deflate"):
        return _Inflater(encoding)
    raise UndecodableBodyError(f"unsupported content-encoding {encoding!r}")


async def read_capped_body(resp: httpx.Response, max_bytes: int) -> bytes:
    """Read a streamed response body, raising ``ResponseTooLargeError`` past ``max_bytes``.

    A well-formed ``Content-Length`` above the cap is rejected before any body
    byte is read; a missing or malformed one falls through to the running
    count. The cap applies to the decoded body: a gzip/deflate body is inflated
    in steps of at most :data:`DECODE_STEP_BYTES`, each counted before the next
    is produced, so peak memory stays within ``max_bytes`` plus one step and
    one raw chunk however far the body would expand. Any other
    ``Content-Encoding`` (or a corrupt compressed stream) raises
    ``UndecodableBodyError``.
    """
    content_length = resp.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > max_bytes:
        raise ResponseTooLargeError
    out = _CappedBuffer(max_bytes)
    if resp.is_stream_consumed:
        # The transport handed back a body it had already read and decoded
        # (``httpx`` does this for in-memory ``ByteStream`` content); there is
        # no raw stream left, so the decoded bytes are capped as they are.
        out.add(resp.content)
        return out.value()
    inflater = _inflater_for(resp)
    async for raw in resp.aiter_raw():
        if inflater is None:
            out.add(raw)
        else:
            inflater.feed(raw, out)
    if inflater is not None:
        inflater.finish(out)
    return out.value()


def decode_body(resp: httpx.Response, body: bytes) -> str:
    """Decode a body read via :func:`read_capped_body` exactly as ``resp.text`` would.

    ``resp.text`` is unavailable on a streamed response read through
    :func:`read_capped_body`; this applies the same charset resolution
    (``Content-Type`` charset, else UTF-8) and ``errors="replace"`` policy.
    """
    return body.decode(resp.encoding or "utf-8", errors="replace")
