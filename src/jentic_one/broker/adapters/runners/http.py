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
raises the domain ``UpstreamTimeoutError`` (timeout) or a generic ``BrokerError``
(other transport errors) so the central handler maps it — the runner never
raises a web/HTTP exception.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import OrderedDict
from collections.abc import AsyncIterator

import httpx
import structlog
from opentelemetry import trace
from opentelemetry.trace import StatusCode

from jentic_one.broker.adapters.runners.base import (
    HTTP_RUNNER_CAPABILITIES,
    RunnerRequest,
    RunnerResult,
    StreamingResult,
    UpstreamRunner,
)
from jentic_one.broker.core.exceptions import (
    BrokerError,
    ErrorOrigin,
    UpstreamResponseTooLargeError,
    UpstreamTimeoutError,
    switch_toolkit_directive,
)
from jentic_one.shared.broker.protocols import RunnerCapabilities

logger = structlog.get_logger(__name__)
_tracer = trace.get_tracer("broker.runner")


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
    def _outbound_headers(headers: dict[str, str]) -> dict[str, str]:
        """Ensure Accept-Encoding is explicit so httpx never injects its default.

        When the caller omits Accept-Encoding, httpx silently adds
        ``gzip, deflate, br`` — causing the upstream to compress a response the
        caller never opted into. We inject ``identity`` as a sentinel to suppress
        the default while signalling "no encoding" to the upstream.
        """
        if any(k.lower() == "accept-encoding" for k in headers):
            return headers
        return {**headers, "accept-encoding": "identity"}

    async def run(self, request: RunnerRequest) -> RunnerResult:
        host = httpx.URL(request.url).host
        async with self._bulkhead.guard(host):
            return await self._run(request, host)

    async def _run(self, request: RunnerRequest, host: str) -> RunnerResult:
        start = time.perf_counter()
        with _tracer.start_as_current_span("broker.upstream_request") as span:
            span.set_attribute("http.url_host", host)
            try:
                req = self._client.build_request(
                    method=request.method,
                    url=request.url,
                    content=request.body,
                    headers=self._outbound_headers(request.headers),
                    timeout=request.timeout_s,
                )
                resp = await self._client.send(req, stream=True)
                try:
                    body = await self._read_capped(resp)
                finally:
                    await resp.aclose()
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                # Connect-phase failure: no request bytes reached the upstream, so
                # a retry is safe for ANY method. ConnectTimeout
                # subclasses both ConnectError and TimeoutException — handle it
                # here, before the generic timeout branch.
                span.record_exception(exc)
                span.set_status(StatusCode.ERROR)
                logger.error("upstream_network_failure", host=host, error_type=type(exc).__name__)
                raise UpstreamTimeoutError(
                    detail="The upstream connection could not be established in time.",
                    origin=ErrorOrigin.UPSTREAM,
                    pre_send=True,
                ) from exc
            except httpx.TimeoutException as exc:
                span.record_exception(exc)
                span.set_status(StatusCode.ERROR)
                logger.error("upstream_network_failure", host=host, error_type=type(exc).__name__)
                raise UpstreamTimeoutError(
                    detail="The upstream did not respond within the deadline.",
                    origin=ErrorOrigin.UPSTREAM,
                ) from exc
            except httpx.HTTPError as exc:
                span.record_exception(exc)
                span.set_status(StatusCode.ERROR)
                logger.error("upstream_network_failure", host=host, error_type=type(exc).__name__)
                raise BrokerError(
                    detail=f"Upstream transport error: {str(exc)[:128]}",
                    origin=ErrorOrigin.UPSTREAM,
                ) from exc

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return RunnerResult(
            status_code=resp.status_code,
            body=body,
            headers=dict(resp.headers),
            content_type=resp.headers.get("content-type"),
            duration_ms=elapsed_ms,
        )

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
        async for chunk in self._cap_iter(resp.aiter_raw(), resp.status_code):
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
                    origin=ErrorOrigin.UPSTREAM,
                    extra={"max_response_bytes": cap},
                    directive=switch_toolkit_directive(status_code),
                )
            yield chunk

    @contextlib.asynccontextmanager
    async def stream(self, request: RunnerRequest) -> AsyncIterator[StreamingResult]:
        """Open the upstream response and stream it without buffering.

        Holds the upstream ``httpx`` response open only for the ``async with``
        body: ``client.stream`` is itself a context manager, so when the caller's
        block exits — normal completion, a size-cap abort, or a client-disconnect
        ``CancelledError`` propagating out of the consuming generator — the
        response is ``aclose()``d and the pool slot released (no zombie drain).
        The yielded ``aiter`` enforces the same mid-stream size cap as
        :meth:`run`. The per-host bulkhead is held for the full stream lifetime.
        """
        host = httpx.URL(request.url).host
        async with self._bulkhead.guard(host):
            try:
                async with self._client.stream(
                    method=request.method,
                    url=request.url,
                    content=request.body,
                    headers=self._outbound_headers(request.headers),
                    timeout=request.timeout_s,
                ) as resp:
                    yield StreamingResult(
                        status_code=resp.status_code,
                        headers=dict(resp.headers),
                        content_type=resp.headers.get("content-type"),
                        aiter=self._cap_iter(resp.aiter_raw(), resp.status_code),
                    )
            except httpx.TimeoutException as exc:
                raise UpstreamTimeoutError(
                    detail="The upstream did not respond within the deadline.",
                    origin=ErrorOrigin.UPSTREAM,
                ) from exc
            except httpx.HTTPError as exc:
                raise BrokerError(
                    detail=f"Upstream transport error: {str(exc)[:128]}",
                    origin=ErrorOrigin.UPSTREAM,
                ) from exc
