"""The default HTTP ``UpstreamRunner`` over a single shared client.

Infrastructure adapter: it owns the transport. The shared bounded
``httpx.AsyncClient`` folds in here — the
client is **injected**, never constructed per-request, so there is one pool per
process shared by the sync handler and the async worker.

It returns the **real** upstream status/headers/body verbatim (no 200
hardcoding, no header dropping) and **streams the raw, still-compressed bytes**
(``aiter_raw``) so httpx never transparently decompresses the upstream body — a
zip-bomb vector for a passthrough that has no reason to decode. Status mirroring
+ header filtering are the caller's concern. On a transport-level failure it
raises a domain error — ``UpstreamTimeoutError`` (504) for a timeout,
``UpstreamTransportError`` (502) when the upstream leg fails, including a host
the DNS-pinning transport (``shared/egress``) cannot resolve at connect time —
so the central handler maps it; the runner never raises a web/HTTP exception.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import structlog
from opentelemetry import trace
from opentelemetry.trace import Span, StatusCode

from jentic_one.broker.adapters.runners.base import (
    HTTP_RUNNER_CAPABILITIES,
    RunnerRequest,
    RunnerResult,
    StreamingResult,
    UpstreamRunner,
)
from jentic_one.broker.core.directives import switch_toolkit_directive
from jentic_one.broker.core.errors import (
    BrokerError,
    UpstreamBodyInterruptedError,
    UpstreamResponseTooLargeError,
    UpstreamTimeoutError,
    UpstreamTransportError,
    UpstreamUrlNotAllowedError,
)
from jentic_one.broker.core.headers import connection_nominated_headers
from jentic_one.broker.core.proxy_headers import ResponseHeaderMap
from jentic_one.shared.broker.protocols import RunnerCapabilities
from jentic_one.shared.egress import UpstreamHostUnresolvedError
from jentic_one.shared.http.injection import (
    filter_inbound_cookie,
    record_upstream_set_cookie,
)
from jentic_one.shared.url_validation import UpstreamUrlInvalidError, reject_url_userinfo

logger = structlog.get_logger(__name__)
_tracer = trace.get_tracer("broker.runner")

#: A connection-phase failure from the client stack: an ``httpx`` error, or a
#: host the DNS-pinning transport cannot resolve. The pinning transport raises
#: the latter before httpx's own transport runs, so httpx never wraps it.
_TransportFailure = httpx.HTTPError | UpstreamHostUnresolvedError
_TRANSPORT_FAILURES = (httpx.HTTPError, UpstreamHostUnresolvedError)


def _transport_error_detail(exc: _TransportFailure) -> str:
    """Caller-facing detail for a transport failure: the exception class only.

    httpx/h11 exception messages can quote request material verbatim (e.g. an
    ``Illegal header value b'...'`` carries the injected credential header), so
    the message text is never surfaced — only the class name, which is safe.
    """
    return f"Upstream transport error ({type(exc).__name__})"


def _record_transport_error(span: Span, exc: _TransportFailure) -> None:
    """Record a transport failure on the span without the exception message.

    ``span.record_exception`` would store ``str(exc)`` plus a traceback that
    repeats it; for the same reason as :func:`_transport_error_detail`, only the
    exception type is recorded.
    """
    span.add_event(
        "exception",
        {
            "exception.type": type(exc).__qualname__,
            "exception.message": _transport_error_detail(exc),
        },
    )
    span.set_status(StatusCode.ERROR)


#: Transport failures on the upstream leg after the connection is established —
#: the connection dropped (including mid-body) or broke the protocol. The broker
#: answers these as a gateway: 502.
_UPSTREAM_LEG_ERRORS = (httpx.NetworkError, httpx.RemoteProtocolError)


def _map_transport_error(exc: _TransportFailure) -> BrokerError:
    """The domain error for a transport failure on either runner path.

    - ``PoolTimeout`` → :class:`UpstreamTimeoutError` (504),
      ``upstream_connect_failed``, ``pre_send``, ``local_only`` (the broker's
      own connection pool had no free slot, so no bytes were sent and the
      target host's circuit breaker is not charged).
    - ``ConnectTimeout`` → :class:`UpstreamTimeoutError` (504),
      ``upstream_connect_failed``, ``pre_send``.
    - Any other timeout → :class:`UpstreamTimeoutError` (504), ``upstream_timeout``.
    - ``ConnectError``, ``ProxyError`` (refused proxy ``CONNECT`` tunnel), or
      :class:`UpstreamHostUnresolvedError` from the DNS-pinning transport →
      :class:`UpstreamTransportError` (502), ``upstream_connect_failed``,
      ``pre_send``.
    - Any other upstream-leg failure (:data:`_UPSTREAM_LEG_ERRORS`) →
      :class:`UpstreamTransportError` (502), ``upstream_transport_error``.
    - Anything else (``LocalProtocolError``: the broker built a request httpx
      refuses to send; ``UnsupportedProtocol``) → a plain :class:`BrokerError`
      (500), ``upstream_request_invalid``.

    ``pre_send`` marks a connect-phase failure: no request bytes reached the
    upstream, so a retry is safe for any method. The caller-facing detail never
    carries the exception message (see :func:`_transport_error_detail`).
    """
    if isinstance(exc, httpx.PoolTimeout):
        return UpstreamTimeoutError(
            detail="The broker's upstream connection pool had no free connection in time.",
            type="upstream_connect_failed",
            pre_send=True,
            local_only=True,
        )
    if isinstance(exc, httpx.ConnectTimeout):
        return UpstreamTimeoutError(
            detail="The upstream connection could not be established in time.",
            type="upstream_connect_failed",
            pre_send=True,
        )
    if isinstance(exc, httpx.TimeoutException):
        return UpstreamTimeoutError(
            detail="The upstream did not respond within the deadline.",
            type="upstream_timeout",
        )
    if isinstance(exc, httpx.ConnectError | httpx.ProxyError | UpstreamHostUnresolvedError):
        return UpstreamTransportError(
            detail="The upstream connection could not be established.",
            type="upstream_connect_failed",
            pre_send=True,
        )
    if isinstance(exc, _UPSTREAM_LEG_ERRORS):
        return UpstreamTransportError(detail=_transport_error_detail(exc))
    return BrokerError(detail=_transport_error_detail(exc), type="upstream_request_invalid")


class _HostSlot:
    """A per-host semaphore plus an in-flight counter (so idle entries are evictable)."""

    __slots__ = ("in_flight", "sem")

    def __init__(self, per_host: int) -> None:
        self.sem = asyncio.Semaphore(per_host)
        self.in_flight = 0


class _HostBulkhead:
    """Per-host concurrency cap (the bulkhead) over a bounded LRU of semaphores.

    httpx has no native per-host limit, so one slow upstream could otherwise
    consume the entire connection budget. We gate each call on a per-host
    ``asyncio.Semaphore``. The broker fronts arbitrary hosts, so the map is
    **bounded**: a capped LRU that only evicts a host with **no in-flight
    calls** — never one that is busy. Beyond the cap, when every entry is busy,
    the new host shares the global pool limit (no per-host gate) rather than
    leaking memory.
    """

    def __init__(self, *, per_host: int, max_hosts: int = 1024) -> None:
        self._per_host = per_host
        self._max_hosts = max_hosts
        self._slots: OrderedDict[str, _HostSlot] = OrderedDict()

    def _slot_for(self, host: str) -> _HostSlot | None:
        slot = self._slots.get(host)
        if slot is not None:
            self._slots.move_to_end(host)
            return slot

        if len(self._slots) >= self._max_hosts:
            self._evict_idle()
        if len(self._slots) >= self._max_hosts:
            # Still full of busy hosts — skip the per-host gate for this call
            # rather than grow unbounded; it falls back to the global pool limit.
            return None

        slot = _HostSlot(self._per_host)
        self._slots[host] = slot
        return slot

    def _evict_idle(self) -> None:
        for host, slot in list(self._slots.items()):
            if slot.in_flight == 0:
                del self._slots[host]
                return

    @contextlib.asynccontextmanager
    async def guard(self, host: str) -> AsyncIterator[None]:
        """Hold a per-host permit for the duration of a call (no-op past the cap)."""
        slot = self._slot_for(host)
        if slot is None:
            yield
            return
        slot.in_flight += 1
        try:
            async with slot.sem:
                yield
        finally:
            slot.in_flight -= 1


@dataclass(frozen=True, slots=True)
class HttpRunnerResult(RunnerResult):
    """HTTP runner response retaining ordered multi-item ``raw_headers``."""

    raw_headers: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class HttpStreamingResult(StreamingResult):
    """HTTP streaming response retaining ordered multi-item ``raw_headers``."""

    raw_headers: tuple[tuple[str, str], ...] = ()


def _parse_request_url(url: str) -> httpx.URL:
    """Parse ``url`` and reject any embedded userinfo before transport dispatch.

    HTTPX derives Basic authentication from URL userinfo and overwrites an
    existing ``Authorization`` header during request authentication. Rejecting
    userinfo here defends the transport edge even if a caller bypasses pre-flight
    URL validation.
    """
    try:
        reject_url_userinfo(url)
    except UpstreamUrlInvalidError as exc:
        raise UpstreamUrlNotAllowedError(
            detail=str(exc),
            type="invalid_upstream_url",
        ) from exc
    parsed = httpx.URL(url)
    if parsed.username or parsed.password or b"@" in parsed.raw_host:
        raise UpstreamUrlNotAllowedError(
            detail="upstream URL must not contain userinfo",
            type="invalid_upstream_url",
        )
    return parsed


class HttpRunner(UpstreamRunner):
    """Executes upstream requests over the single shared bounded ``httpx.AsyncClient``.

    The client is injected (built once in the app lifespan); the runner never
    constructs a per-request client. Concurrency to any one upstream host is
    capped by the per-host bulkhead.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        max_per_host: int = 50,
        max_response_bytes: int = 0,
    ) -> None:
        self._client = client
        self._bulkhead = _HostBulkhead(per_host=max_per_host)
        # 0 disables the response-size cap (unbounded). Enforced mid-stream in
        # ``_read_capped`` so a hostile/large upstream can't OOM the instance.
        self._max_response_bytes = max_response_bytes

    def capabilities(self) -> RunnerCapabilities:
        """HTTP supports the full envelope: async, idempotency, retries."""
        return HTTP_RUNNER_CAPABILITIES

    @staticmethod
    def _outbound_headers(headers: dict[str, str], url: str | None = None) -> dict[str, str]:
        """Ensure Accept-Encoding is explicit and strip Connection-nominated or foreign cookies.

        When the caller omits Accept-Encoding, httpx silently adds
        ``gzip, deflate, br`` — causing the upstream to compress a response the
        caller never opted into. We inject ``identity`` as a sentinel to suppress
        the default while signalling "no encoding" to the upstream.
        """
        nominated = connection_nominated_headers(headers)
        out: dict[str, str] = {}
        for key, value in headers.items():
            lowered = key.lower()
            if lowered == "connection" or lowered in nominated:
                continue
            if lowered == "cookie" and url:
                filtered_cookie = filter_inbound_cookie(value, url, allow_untracked=True)
                if not filtered_cookie:
                    continue
                value = filtered_cookie
            out[key] = value
        if any(k.lower() == "accept-encoding" for k in out):
            return out
        return {**out, "accept-encoding": "identity"}

    async def run(self, request: RunnerRequest) -> HttpRunnerResult:
        host = _parse_request_url(request.url).host
        async with self._bulkhead.guard(host):
            return await self._run(request, host)

    async def _run(self, request: RunnerRequest, host: str) -> HttpRunnerResult:
        start = time.perf_counter()
        with _tracer.start_as_current_span("broker.upstream_request") as span:
            span.set_attribute("http.url_host", host)
            try:
                req = self._client.build_request(
                    method=request.method,
                    url=request.url,
                    content=request.body,
                    headers=self._outbound_headers(request.headers, request.url),
                    timeout=request.timeout_s,
                )
                resp = await self._client.send(req, stream=True)
                try:
                    body = await self._read_capped(resp)
                finally:
                    await resp.aclose()
            except _TRANSPORT_FAILURES as exc:
                # One mapping for every transport failure (see
                # ``_map_transport_error``); a connect-phase failure is
                # ``pre_send`` so the retry decorator may retry it for any method.
                _record_transport_error(span, exc)
                logger.error("upstream_network_failure", host=host, error_type=type(exc).__name__)
                # ``from None``: a chained cause would put the raw message back
                # into any traceback rendered from this error (span, log).
                raise _map_transport_error(exc) from None

        elapsed_ms = (time.perf_counter() - start) * 1000
        raw_headers = tuple((k, v) for k, v in resp.headers.multi_items())
        for header_name, header_val in raw_headers:
            if header_name.lower() == "set-cookie":
                record_upstream_set_cookie(request.url, header_val)
        return HttpRunnerResult(
            status_code=resp.status_code,
            body=body,
            headers=ResponseHeaderMap(dict(resp.headers), raw_headers=raw_headers),
            content_type=resp.headers.get("content-type"),
            duration_ms=elapsed_ms,
            raw_headers=raw_headers,
        )

    @staticmethod
    async def _raw_source(resp: httpx.Response) -> AsyncIterator[bytes]:
        if resp.is_stream_consumed:
            raw = getattr(resp, "_raw_content", None)
            payload = raw if isinstance(raw, bytes) else resp.content
            if payload:
                yield payload
            return
        async for chunk in resp.aiter_raw():
            yield chunk

    async def _read_capped(self, resp: httpx.Response) -> bytes:
        """Drain the raw (still-compressed) body, enforcing the size cap mid-stream.

        Reads ``aiter_raw`` chunk-by-chunk and aborts the moment the running
        total exceeds ``max_response_bytes`` — *before* the whole oversized body
        is materialised — raising :class:`UpstreamResponseTooLargeError`. The
        caller's ``finally`` ``aclose()``s the response, tearing down the upstream
        connection so a hostile/large upstream can't OOM the instance or pin a
        pool slot. A cap of 0 disables the check (unbounded passthrough).
        """
        chunks: list[bytes] = []
        async for chunk in self._cap_iter(self._raw_source(resp), resp.status_code):
            chunks.append(chunk)
        return b"".join(chunks)

    async def _cap_iter(
        self, source: AsyncIterator[bytes], status_code: int
    ) -> AsyncIterator[bytes]:
        """Re-yield ``source`` chunks, enforcing ``max_response_bytes`` mid-stream.

        Shared by the buffered read (:meth:`_read_capped`) and the streaming
        passthrough (:meth:`stream`): the cap is enforced on the raw byte total so
        a hostile/large upstream is cut off the instant it overruns, before the
        oversized body is materialised or forwarded. A cap of 0 disables it.
        """
        cap = self._max_response_bytes
        total = 0
        async for chunk in source:
            total += len(chunk)
            if cap and total > cap:
                raise UpstreamResponseTooLargeError(
                    detail=f"The upstream response exceeded the {cap}-byte response-size cap.",
                    type="upstream_response_too_large",
                    extra={"max_response_bytes": cap},
                    directive=switch_toolkit_directive(status_code),
                )
            yield chunk

    async def _stream_body(self, resp: httpx.Response) -> AsyncIterator[bytes]:
        """The streaming passthrough's body: :meth:`_cap_iter` with transport errors mapped.

        A failure here happens after the response has started (the upstream
        dropped or stalled partway through the body), so it is raised as
        :class:`UpstreamBodyInterruptedError` for the streaming edge to log,
        abort and record — never as a raw ``httpx`` exception, and never as the
        open-phase error a failure before the headers maps to. Its
        ``transport_error`` names the failure class; the message is never
        surfaced (see :func:`_transport_error_detail`).
        """
        try:
            async for chunk in self._cap_iter(self._raw_source(resp), resp.status_code):
                yield chunk
        except httpx.TimeoutException as exc:
            raise UpstreamBodyInterruptedError(
                detail="The upstream stopped sending the response body within the deadline.",
                type="upstream_timeout",
                transport_error=type(exc).__name__,
            ) from None
        except httpx.HTTPError as exc:
            raise UpstreamBodyInterruptedError(
                detail=_transport_error_detail(exc),
                type="upstream_transport_error",
                transport_error=type(exc).__name__,
            ) from None

    @contextlib.asynccontextmanager
    async def stream(self, request: RunnerRequest) -> AsyncIterator[HttpStreamingResult]:
        """Open the upstream response and stream it without buffering.

        Holds the upstream ``httpx`` response open only for the ``async with``
        body: ``client.stream`` is itself a context manager, so when the caller's
        block exits — normal completion, a size-cap abort, or a client-disconnect
        ``CancelledError`` propagating out of the consuming generator — the
        response is ``aclose()``d and the pool slot released (no zombie drain).
        The yielded ``aiter`` enforces the same mid-stream size cap as
        :meth:`run` and raises a mid-body transport failure as a domain error
        (see :meth:`_stream_body`). The per-host bulkhead is held for the full
        stream lifetime.
        """
        host = _parse_request_url(request.url).host
        async with self._bulkhead.guard(host):
            try:
                async with self._client.stream(
                    method=request.method,
                    url=request.url,
                    content=request.body,
                    headers=self._outbound_headers(request.headers, request.url),
                    timeout=request.timeout_s,
                ) as resp:
                    raw_headers = tuple((k, v) for k, v in resp.headers.multi_items())
                    for header_name, header_val in raw_headers:
                        if header_name.lower() == "set-cookie":
                            record_upstream_set_cookie(request.url, header_val)
                    yield HttpStreamingResult(
                        status_code=resp.status_code,
                        headers=ResponseHeaderMap(dict(resp.headers), raw_headers=raw_headers),
                        content_type=resp.headers.get("content-type"),
                        aiter=self._stream_body(resp),
                        raw_headers=raw_headers,
                    )
            except _TRANSPORT_FAILURES as exc:
                # Same mapping as the buffered path. The edge drains the body
                # outside this block (its exit stack), so what lands here is a
                # failure opening the stream — before any response has started,
                # while the status is still the broker's to choose.
                logger.error("upstream_network_failure", host=host, error_type=type(exc).__name__)
                # ``from None``: a chained cause would put the raw message back
                # into any traceback rendered from this error (span, log).
                raise _map_transport_error(exc) from None
