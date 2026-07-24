"""Streaming-passthrough web edge for the sync proxy (§08 E2.4).

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

from fastapi import Response
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from jentic_one.broker.adapters.runners.base import (
    RunnerRequest,
    StreamingResult,
    StreamingUpstreamRunner,
)
from jentic_one.broker.core.exceptions import ErrorOrigin, UpstreamTimeoutError
from jentic_one.broker.core.headers import REGION_MISMATCH_HINT, JenticHeader
from jentic_one.broker.core.proxy_headers import passthrough_streaming_headers
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.shared.broker.execution import StreamingOutcome

__all__ = [
    "StreamingOutcome",
    "guarded_body",
    "open_streaming_response",
]


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
                    origin=ErrorOrigin.UPSTREAM,
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
                    origin=ErrorOrigin.UPSTREAM,
                ) from exc
            except asyncio.CancelledError:
                if outcome is not None:
                    outcome.error = "client_disconnect"
                raise
            except Exception as exc:
                if outcome is not None:
                    outcome.error = f"upstream_error: {type(exc).__name__}"
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
            outcome.duration_ms = int((time.perf_counter() - outcome.started_at_perf) * 1000)
        await stack.aclose()


def _metadata_headers(
    ctx_req: ExecuteRequestContext, execution_id: str, status_code: int
) -> dict[str, str]:
    metadata: dict[str, str] = {
        JenticHeader.EXECUTION_ID.value: execution_id,
        JenticHeader.UPSTREAM_STATUS.value: str(status_code),
    }
    if ctx_req.toolkit_id:
        metadata[JenticHeader.TOOLKIT_ID.value] = ctx_req.toolkit_id
    if ctx_req.operation_id:
        metadata[JenticHeader.OPERATION.value] = ctx_req.operation_id
    if ctx_req.api_vendor:
        metadata[JenticHeader.API_VENDOR.value] = ctx_req.api_vendor
    # Credential attribution (#740). Absent when no credential was used, so
    # the streaming path stays symmetric with the sync router.
    if ctx_req.credential_id:
        metadata[JenticHeader.CREDENTIAL_ID.value] = ctx_req.credential_id
    if ctx_req.credential_name:
        metadata[JenticHeader.CREDENTIAL_NAME.value] = ctx_req.credential_name
    if status_code >= 400:
        metadata[JenticHeader.ERROR_ORIGIN.value] = ErrorOrigin.UPSTREAM.value
    # Region-mismatch hint for a templated-host API's upstream 401/403 (#638),
    # surfaced via header — the streamed upstream body stays verbatim.
    if status_code in (401, 403) and ctx_req.has_server_variable:
        metadata[JenticHeader.HINT.value] = REGION_MISMATCH_HINT
    return metadata


async def open_streaming_response(
    runner: StreamingUpstreamRunner,
    request: RunnerRequest,
    ctx_req: ExecuteRequestContext,
    execution_id: str,
    *,
    transfer_deadline_s: float,
    background_callback: Callable[[StreamingOutcome], Awaitable[None]] | None = None,
) -> Response:
    """Open the upstream stream and return a `StreamingResponse` over its body.

    The runner's `stream()` context is entered into an `AsyncExitStack` so it
    stays open for the response body's lifetime and is unwound (upstream
    `aclose()`d) when the body generator finishes or is cancelled on client
    disconnect.

    When ``background_callback`` is provided, it is invoked as a Starlette
    ``BackgroundTask`` after the response body completes — used for persistence.
    """
    stack = AsyncExitStack()
    try:
        result = await stack.enter_async_context(runner.stream(request))
    except BaseException:
        await stack.aclose()
        raise

    outcome: StreamingOutcome | None = None
    background: BackgroundTask | None = None
    if background_callback is not None:
        outcome = StreamingOutcome(
            execution_id=execution_id,
            http_status=result.status_code,
        )
        background = BackgroundTask(background_callback, outcome)

    metadata = _metadata_headers(ctx_req, execution_id, result.status_code)
    passthrough = passthrough_streaming_headers(result.headers)
    body = guarded_body(result, stack, transfer_deadline_s=transfer_deadline_s, outcome=outcome)
    return StreamingResponse(
        body,
        status_code=result.status_code,
        media_type=result.content_type,
        headers={**passthrough, **metadata},
        background=background,
    )
