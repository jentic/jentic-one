"""Streaming-passthrough web edge for the sync proxy.

The buffered path (`RunnerResult.body`) is kept for idempotent requests (replay
needs the whole body) and the async worker (persists the body). This module is
the **streaming** counterpart used for sync, non-idempotent requests: it forwards
the upstream body straight to the client without whole-buffering, while enforcing

  * the mid-stream **response-size cap** (inherited from the runner's `aiter`),
  * an overall **transfer deadline** (the whole-stream budget — `read_timeout_s`
    only bounds the *gap* between bytes, so a steady trickle, or a slow client
    draining the proxied body, could otherwise pin a pool slot far longer than
    intended), and
  * **client-disconnect teardown**: when the downstream aborts, Starlette cancels
    the body generator; the `AsyncExitStack` unwinds and `aclose()`s the upstream
    `httpx` stream (releasing the pool slot) instead of leaking a zombie drain.
    `CancelledError` is re-raised after cleanup, never swallowed.

A failure partway through the body (the upstream drops, stalls, or overruns a
cap) arrives after the status line is sent, so it cannot become an error
response: `OutcomeStreamingResponse` logs one structured event for it and ends
the response without its final body frame, so the server closes the connection
and the client sees a truncated body rather than a clean end. On every outcome
it then hands the `StreamingOutcome` to the completion callback exactly once —
the execution record is never lost to a body that raised. A stream that ends
before any response exists (it fails to open, or the call is cancelled before
the headers arrive) hands the callback a FAILED outcome with no upstream
status, then re-raises unchanged. The callback must return promptly: the
router's callback hands the record write to the app's background-task registry
(`jentic_one.shared.web.background_tasks`), so neither a clean end, an abort,
nor an error response waits on the database.

The truncation is detectable only where the downstream framing delimits the
body: HTTP/1.1 chunked (the broker strips the upstream ``Content-Length``, so a
streamed response is always chunked) and HTTP/2. An HTTP/1.0 client, or a front
proxy that re-frames the body, reads a close-delimited body, and an aborted
body looks complete to it.

Deferred: a dedicated per-chunk slow-client *write* timeout — the downstream
socket write happens inside the ASGI server (outside this generator), so it can't
be wrapped here cleanly; the whole-stream transfer deadline already bounds a slow
reader's total hold on the connection.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack
from urllib.parse import urlsplit

import structlog
from fastapi import Response
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from jentic_one.broker.adapters.runners.base import (
    RunnerRequest,
    StreamingResult,
    StreamingUpstreamRunner,
)
from jentic_one.broker.core.errors import (
    BrokerError,
    CircuitOpenError,
    UpstreamBodyInterruptedError,
    UpstreamTimeoutError,
)
from jentic_one.broker.core.headers import (
    REGION_MISMATCH_HINT,
    TRACESTATE_HEADER,
    JenticHeader,
    header_safe_value,
)
from jentic_one.broker.core.proxy_headers import passthrough_streaming_headers
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.shared.broker.execution import (
    ErrorOrigin,
    StreamingOutcome,
)
from jentic_one.shared.logging import mark_response_aborted
from jentic_one.shared.tracing import JENTIC_TRACESTATE_KEY, pack_jentic_tracestate

__all__ = [
    "OutcomeStreamingResponse",
    "StreamingOutcome",
    "guarded_body",
    "open_streaming_response",
]

logger = structlog.get_logger(__name__)


def _error_type(exc: Exception) -> str:
    """The failure class to record: a mid-body drop's transport error, else the exception's."""
    if isinstance(exc, UpstreamBodyInterruptedError):
        return exc.transport_error
    return type(exc).__name__


def _pre_header_error(exc: BaseException) -> str:
    """The execution record's ``error`` for a call that ends before its response headers.

    A cancellation records ``cancelled``; a mapped domain failure records its
    problem ``type`` — the identifier the caller receives in the problem+json
    body, so the record names the failure the same way the response does;
    anything else records its exception class. Never the ``detail`` or the
    exception message, which can quote request material.
    """
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    if isinstance(exc, BrokerError) and exc.type != BrokerError.default_type:
        return exc.type
    return f"server_error: {type(exc).__name__}"


async def guarded_body(
    result: StreamingResult,
    stack: AsyncExitStack,
    *,
    transfer_deadline_s: float,
    outcome: StreamingOutcome | None = None,
) -> AsyncGenerator[bytes, None]:
    """Yield upstream chunks under an overall transfer deadline.

    Owns the open-stream lifetime: the `AsyncExitStack` (which holds the runner's
    `stream()` context) is closed in `finally`, so any exit path — normal EOF, a
    deadline/size-cap abort, or a client-disconnect `CancelledError` — tears down
    the upstream `httpx` response and releases the pool slot. `CancelledError`
    propagates (re-raised by the `finally`'s implicit re-raise) so Starlette still
    sees the disconnect; it is never swallowed.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + transfer_deadline_s if transfer_deadline_s > 0 else None
    aiter = result.aiter
    try:
        while True:
            remaining = None if deadline is None else deadline - loop.time()
            if remaining is not None and remaining <= 0:
                if outcome is not None:
                    outcome.error = "transfer_deadline_exceeded"
                raise UpstreamTimeoutError(
                    detail="The upstream response exceeded the transfer deadline.",
                    type="upstream_timeout",
                )
            try:
                chunk = await (
                    asyncio.wait_for(aiter.__anext__(), remaining)
                    if remaining is not None
                    else aiter.__anext__()
                )
            except StopAsyncIteration:
                return
            except TimeoutError as exc:
                if outcome is not None:
                    outcome.error = "transfer_deadline_exceeded"
                raise UpstreamTimeoutError(
                    detail="The upstream response exceeded the transfer deadline.",
                    type="upstream_timeout",
                ) from exc
            except asyncio.CancelledError:
                if outcome is not None:
                    outcome.error = "client_disconnect"
                raise
            except Exception as exc:
                if outcome is not None:
                    outcome.error = f"upstream_error: {_error_type(exc)}"
                raise
            if outcome is not None:
                outcome.bytes_transferred += len(chunk)
            yield chunk
    except GeneratorExit:
        if outcome is not None and outcome.error is None:
            outcome.error = "client_disconnect"
        raise
    finally:
        if outcome is not None:
            outcome.duration_ms = (time.perf_counter() - outcome.started_at_perf) * 1000
        await stack.aclose()


def _metadata_headers(
    ctx_req: ExecuteRequestContext, execution_id: str, status_code: int
) -> dict[str, str]:
    metadata: dict[str, str] = {
        JenticHeader.EXECUTION_ID.value: execution_id,
        JenticHeader.UPSTREAM_STATUS.value: str(status_code),
    }
    if ctx_req.operation:
        metadata[JenticHeader.OPERATION.value] = ctx_req.operation.id
    if ctx_req.api_vendor:
        metadata[JenticHeader.API_VENDOR.value] = ctx_req.api_vendor
    # Credential attribution (#740). Absent when no credential was used, so
    # the streaming path stays symmetric with the sync router. The name is
    # operator-authored free text: sanitize before emission.
    if ctx_req.credential_id:
        metadata[JenticHeader.CREDENTIAL_ID.value] = ctx_req.credential_id
    if ctx_req.credential_name:
        metadata[JenticHeader.CREDENTIAL_NAME.value] = header_safe_value(ctx_req.credential_name)
    if status_code >= 400:
        metadata[JenticHeader.ERROR_ORIGIN.value] = ErrorOrigin.UPSTREAM.value
    # Region-mismatch hint for a templated-host API's upstream 401/403 (#638),
    # surfaced via header — the streamed upstream body stays verbatim.
    if status_code in (401, 403) and ctx_req.has_server_variable:
        metadata[JenticHeader.HINT.value] = REGION_MISMATCH_HINT
    # Echo the jentic= tracestate member, identically to the buffered sync route
    # (``routers/execute._metadata_headers``): the header is declared on every
    # successful response and streaming is the default sync path, so omitting it
    # here would leave most 200s without it. ``pack_jentic_tracestate`` is pure —
    # it reads only the request context, so the open upstream stream is irrelevant.
    member = pack_jentic_tracestate(
        execution_id=execution_id,
        toolkit_id=ctx_req.toolkit_id,
        vendor=ctx_req.api_vendor,
        name=ctx_req.api_name,
        version=ctx_req.api_version,
    )
    metadata[TRACESTATE_HEADER] = f"{JENTIC_TRACESTATE_KEY}={member}"
    return metadata


class OutcomeStreamingResponse(StreamingResponse):
    """A ``StreamingResponse`` that settles its ``StreamingOutcome`` on every exit path.

    * A body failure after the status line is sent (an upstream drop or stall,
      a size-cap or transfer-deadline abort) is logged once, with the request's
      correlation fields, and the response ends **without** its final body
      frame: the server then closes the connection, so the client sees a
      truncated body instead of a clean end, and nothing escapes to the
      unhandled-exception path.
    * After the body — completed, failed, or cut off by a client disconnect or
      cancellation — the body generator is closed (finalising the outcome and
      releasing the upstream stream) and ``on_complete`` receives the outcome
      exactly once, even when a cancellation interrupts the close. It never
      raises. ``on_complete`` runs before ``__call__`` returns, so it must
      return promptly (hand slow work such as a DB write to the app's
      background-task registry): the server closes an aborted response's
      connection only once ``__call__`` returns.

    A body that raises would skip a ``BackgroundTask``, so the completion step
    must run in ``__call__``'s ``finally``.
    """

    def __init__(
        self,
        content: AsyncGenerator[bytes, None],
        *,
        stack: AsyncExitStack,
        outcome: StreamingOutcome,
        on_complete: Callable[[StreamingOutcome], Awaitable[None]] | None,
        host: str | None,
        trace_id: str | None,
        status_code: int,
        media_type: str | None,
        headers: dict[str, str],
    ) -> None:
        super().__init__(content, status_code=status_code, media_type=media_type, headers=headers)
        self._body = content
        self._stack = stack
        self._outcome = outcome
        self._on_complete = on_complete
        self._host = host
        self._trace_id = trace_id
        self._aborted = False
        self._body_started = False
        self._completed = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            if self._aborted:
                # Set in the request task's own context, which the server's
                # incomplete-response log call shares.
                mark_response_aborted()
            await self._complete()

    async def stream_response(self, send: Send) -> None:
        await send(
            {"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers}
        )
        self._body_started = True
        while True:
            try:
                chunk = await self._body.__anext__()
            except StopAsyncIteration:
                break
            except Exception as exc:
                self._aborted = True
                self._log_abort(exc)
                return
            await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    def _log_abort(self, exc: Exception) -> None:
        fields = {
            "execution_id": self._outcome.execution_id,
            "trace_id": self._trace_id,
            "host": self._host,
            "upstream_status": self._outcome.http_status,
            "bytes_transferred": self._outcome.bytes_transferred,
            "error_type": _error_type(exc),
            "stream": True,
        }
        if isinstance(exc, UpstreamBodyInterruptedError):
            # The upstream leg failed (drop, protocol error, stall).
            logger.error("upstream_network_failure", **fields)
        elif isinstance(exc, BrokerError):
            # The broker cut the body off (response-size cap, transfer deadline);
            # ``reason`` is what the execution record stores.
            logger.warning("streaming_response_aborted", reason=self._outcome.error, **fields)
        else:
            # Not a mapped upstream/limit failure: a defect, keep the traceback.
            logger.error("streaming_body_failed", exc_info=exc, **fields)

    async def _complete(self) -> None:
        if self._completed:
            return
        self._completed = True
        # Closing an unfinished body (the client went away mid-send) runs its
        # ``finally``: the outcome records the disconnect and its duration
        # before its first await, then the upstream stream is released. A
        # finished body ignores this. A body that never started has no
        # ``finally`` to run, so the stack (whose second close is a no-op) is
        # closed here too and the outcome settled. The hand-off runs in a
        # ``finally`` so a cancellation landing mid-close still delivers it.
        try:
            await self._body.aclose()
            await self._stack.aclose()
        except Exception:
            logger.warning(
                "streaming_body_close_failed",
                execution_id=self._outcome.execution_id,
                exc_info=True,
            )
        finally:
            if not self._body_started:
                self._outcome.duration_ms = int(
                    (time.perf_counter() - self._outcome.started_at_perf) * 1000
                )
                self._outcome.error = self._outcome.error or "client_disconnect"
            await _deliver_outcome(self._outcome, self._on_complete)


async def _deliver_outcome(
    outcome: StreamingOutcome,
    on_complete: Callable[[StreamingOutcome], Awaitable[None]] | None,
) -> None:
    """Hand ``outcome`` to the completion callback; a callback failure never reaches the caller.

    Both hand-off sites run from a ``finally`` (or an ``except`` that re-raises)
    and owe the caller its own outcome afterwards — the response already sent,
    or the mapped problem+json of the failure that ended the call. A callback
    that raises must not replace it, so a ``CancelledError`` out of the
    callback is logged and swallowed along with every other ``Exception``: the
    callback's job is to hand the record write to the app's background-task
    registry, which outlives the request task, so no work is left here for a
    cancellation to stop. ``KeyboardInterrupt``/``SystemExit`` propagate.
    """
    if on_complete is None:
        return
    try:
        await on_complete(outcome)
    except (Exception, asyncio.CancelledError):
        logger.error(
            "streaming_outcome_callback_failed",
            execution_id=outcome.execution_id,
            exc_info=True,
        )


async def _record_pre_header_failure(
    exc: BaseException,
    *,
    execution_id: str,
    started_at_perf: float,
    on_complete: Callable[[StreamingOutcome], Awaitable[None]] | None,
) -> None:
    """Settle the outcome of a stream that ended before any response existed.

    No ``OutcomeStreamingResponse`` is built on this path, so its completion
    step never runs: this is the outcome's only delivery. The outcome carries
    no upstream status, and names the failure with :func:`_pre_header_error`.

    A cancellation here comes from server shutdown or an outer cancel — uvicorn
    does not cancel a handler when its client disconnects — and the streaming
    path serves non-idempotent calls, so the request may already have reached
    the upstream: it must not go unrecorded. It is marked ``cancelled``, so the
    record is written without the action-required event and repeated-failure
    accounting a real failure gets: a rolling restart with streams in flight is
    the server's doing, not the agent's.

    ``circuit_open`` is set for a breaker refusal that holds the breaker's
    per-host report claim, so the persistence path raises the same
    rate-limited ``upstream.circuit_open`` event the buffered path does.

    The callback returns promptly (the router's only schedules the write on the
    app's background-task registry), so the error response never waits on the
    database.
    """
    outcome = StreamingOutcome(
        execution_id=execution_id,
        http_status=None,
        started_at_perf=started_at_perf,
        duration_ms=int((time.perf_counter() - started_at_perf) * 1000),
        error=_pre_header_error(exc),
        circuit_open=isinstance(exc, CircuitOpenError) and exc.report,
        cancelled=isinstance(exc, asyncio.CancelledError),
    )
    await _deliver_outcome(outcome, on_complete)


async def open_streaming_response(
    runner: StreamingUpstreamRunner,
    request: RunnerRequest,
    ctx_req: ExecuteRequestContext,
    execution_id: str,
    *,
    transfer_deadline_s: float,
    background_callback: Callable[[StreamingOutcome], Awaitable[None]] | None = None,
) -> Response:
    """Open the upstream stream and return an `OutcomeStreamingResponse` over its body.

    The runner's `stream()` context is entered into an `AsyncExitStack` so it
    stays open for the response body's lifetime and is unwound (upstream
    `aclose()`d) when the body generator finishes or is cancelled on client
    disconnect.

    When ``background_callback`` is provided, it is the response's completion
    callback: it receives the final ``StreamingOutcome`` exactly once after the
    response body ends, whatever the outcome (completed, failed mid-body,
    client disconnect), before the response returns to the server. A failure
    opening the stream (connect/TLS failure, an open circuit, a timeout before
    the response headers) or a cancellation before them delivers a FAILED
    outcome with no upstream status instead, then propagates unchanged — the
    caller still gets the mapped problem+json response, and cancellation
    semantics are preserved. It must return promptly — the router's callback
    only schedules the record write on the app's background-task registry.
    (The parameter name is part of the ``Broker.execute_streaming`` injection
    seam and stays stable.)
    """
    # Anchor the duration clock *before* the upstream stream is opened:
    # ``runner.stream()`` awaits connect + request send + response headers, and
    # ``StreamingOutcome``'s default ``started_at_perf`` (perf_counter at
    # construction) would run only after all of that — recording body-drain
    # time (~0ms for small responses) instead of the full round trip.
    start_perf = time.perf_counter()
    stack = AsyncExitStack()
    try:
        result = await stack.enter_async_context(runner.stream(request))
    except BaseException as exc:
        # ``enter_async_context`` registers its unwind callback only once
        # ``__aenter__`` returns, so a hand-off that raises leaves the stack
        # empty: whatever the runner opened it has already released itself on
        # the way out, and there is nothing to close here. A mapped failure or
        # a cancellation is recorded — the call may have reached the upstream —
        # while a ``KeyboardInterrupt``/``SystemExit`` means the process is
        # going down, so it gets out of the way without touching the database.
        # The hand-off never raises, so ``raise`` re-raises ``exc`` unchanged.
        if isinstance(exc, Exception | asyncio.CancelledError):
            await _record_pre_header_failure(
                exc,
                execution_id=execution_id,
                started_at_perf=start_perf,
                on_complete=background_callback,
            )
        raise

    outcome = StreamingOutcome(
        execution_id=execution_id,
        http_status=result.status_code,
        started_at_perf=start_perf,
    )
    metadata = _metadata_headers(ctx_req, execution_id, result.status_code)
    passthrough = passthrough_streaming_headers(result.headers)
    body = guarded_body(result, stack, transfer_deadline_s=transfer_deadline_s, outcome=outcome)
    return OutcomeStreamingResponse(
        body,
        stack=stack,
        outcome=outcome,
        on_complete=background_callback,
        host=urlsplit(request.url).hostname,
        trace_id=ctx_req.trace_id,
        status_code=result.status_code,
        media_type=result.content_type,
        headers={**passthrough, **metadata},
    )
