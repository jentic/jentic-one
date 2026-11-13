"""Best-effort streaming execution outcome persistence and concurrency gate.

Runs post-response on the background-task registry, bounding concurrent admin
transactions per ``Context`` on the active event loop and emitting a circuit
fast-fail's ``upstream.circuit_open`` event ahead of the slot wait.
"""

from __future__ import annotations

import asyncio
import weakref
from dataclasses import dataclass
from datetime import datetime

import structlog

from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.services.execution.service import (
    emit_circuit_open_event,
    persist_streaming_execution,
)
from jentic_one.shared.broker.execution import StreamingOutcome
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.events.repeated_failure import RepeatedFailureCounter
from jentic_one.shared.metrics import get_meter
from jentic_one.shared.models import ExecutionStatus

logger = structlog.get_logger(__name__)

_meter = get_meter("broker")
_streaming_persist_failures = _meter.create_counter(
    "broker.streaming_execution.persist_failures",
    unit="{persist}",
    description=(
        "Streaming execution records not persisted, by reason: write_error (the "
        "write raised), backlog_timeout (no slot within "
        "broker.streaming_persist_acquire_timeout_s) or cancelled (abandoned "
        "while waiting or writing; a record cancelled while writing may still "
        "have landed)"
    ),
)
_streaming_persist_pending = _meter.create_up_down_counter(
    "broker.streaming_execution.persist_pending",
    unit="{persist}",
    description=(
        "Streaming execution persists in this process that have not finished, by "
        "state: waiting (queued for a broker.streaming_persist_max_concurrency "
        "slot) or in_flight (holding a slot and writing)"
    ),
)
_PENDING_WAITING = {"state": "waiting"}
_PENDING_IN_FLIGHT = {"state": "in_flight"}
# The ``error`` recorded for an outcome that has no upstream status and names
# no failure — a breach of the ``StreamingOutcome`` contract by an injected
# broker, recorded rather than mistaken for a completed call.
_MISSING_OUTCOME_ERROR = "server_error: missing_outcome_error"
_REASON_WRITE_ERROR = {"reason": "write_error"}
_REASON_BACKLOG_TIMEOUT = {"reason": "backlog_timeout"}
_REASON_CANCELLED = {"reason": "cancelled"}


@dataclass(frozen=True, slots=True)
class _StreamingPersistGate:
    """The semaphore bounding one Context's concurrent streaming persists."""

    loop: asyncio.AbstractEventLoop
    limit: int
    semaphore: asyncio.Semaphore


# One gate per Context (its admin pool), so two Contexts in one process never
# share or thrash a semaphore. A gate is rebuilt only when the running event
# loop changes: one Semaphore must not straddle loops.
_persist_gates: weakref.WeakKeyDictionary[Context, _StreamingPersistGate] = (
    weakref.WeakKeyDictionary()
)


def _effective_persist_limit(config: AppConfig) -> int:
    """``streaming_persist_max_concurrency`` clamped to ``admin.pool_max - 1`` (floor 1)."""
    return min(
        config.broker.streaming_persist_max_concurrency,
        max(1, config.databases.admin.pool_max - 1),
    )


def _streaming_persist_gate(ctx: Context) -> _StreamingPersistGate:
    """The gate bounding ``ctx``'s concurrent streaming persists on this loop.

    Logs ``streaming_persist_limit_clamped`` when the gate is built with a
    limit below the configured one, so the effective cap is never silent.
    """
    loop = asyncio.get_running_loop()
    gate = _persist_gates.get(ctx)
    if gate is None or gate.loop is not loop:
        limit = _effective_persist_limit(ctx.config)
        configured = ctx.config.broker.streaming_persist_max_concurrency
        if limit < configured:
            logger.warning(
                "streaming_persist_limit_clamped",
                configured=configured,
                effective=limit,
                admin_pool_max=ctx.config.databases.admin.pool_max,
            )
        gate = _StreamingPersistGate(loop=loop, limit=limit, semaphore=asyncio.Semaphore(limit))
        _persist_gates[ctx] = gate
    return gate


def _record_persist_failed(outcome: StreamingOutcome, ctx_req: ExecuteRequestContext) -> None:
    """Count and log a persist whose write raised (call from an ``except``)."""
    _streaming_persist_failures.add(1, _REASON_WRITE_ERROR)
    logger.error(
        "streaming_execution_persist_failed",
        execution_id=outcome.execution_id,
        trace_id=ctx_req.trace_id,
        reason=_REASON_WRITE_ERROR["reason"],
        exc_info=True,
    )


def _record_persist_abandoned(
    outcome: StreamingOutcome,
    ctx_req: ExecuteRequestContext,
    *,
    reason: dict[str, str],
    stage: str,
    **fields: object,
) -> None:
    """Count and log a persist given up before it finished.

    ``stage`` is ``waiting`` (no slot yet, so nothing was written) or
    ``writing`` (cancelled inside the transaction, so the record may or may not
    have landed). An expected condition, so no traceback.
    """
    _streaming_persist_failures.add(1, reason)
    logger.error(
        "streaming_execution_persist_abandoned",
        execution_id=outcome.execution_id,
        trace_id=ctx_req.trace_id,
        reason=reason["reason"],
        stage=stage,
        **fields,
    )


async def _acquire_persist_slot(
    semaphore: asyncio.Semaphore,
    timeout_s: float,
    outcome: StreamingOutcome,
    ctx_req: ExecuteRequestContext,
) -> bool:
    """Wait up to ``timeout_s`` for a persist slot; ``False`` when it timed out.

    A timeout or a cancellation while waiting is counted and logged as
    abandoned; the cancellation is re-raised. No permit is held on any path
    that does not return ``True``.
    """
    _streaming_persist_pending.add(1, _PENDING_WAITING)
    try:
        async with asyncio.timeout(timeout_s):
            await semaphore.acquire()
    except TimeoutError:
        _record_persist_abandoned(
            outcome,
            ctx_req,
            reason=_REASON_BACKLOG_TIMEOUT,
            stage="waiting",
            acquire_timeout_s=timeout_s,
        )
        return False
    except asyncio.CancelledError:
        _record_persist_abandoned(outcome, ctx_req, reason=_REASON_CANCELLED, stage="waiting")
        raise
    finally:
        _streaming_persist_pending.add(-1, _PENDING_WAITING)
    return True


async def _emit_streaming_circuit_event(
    outcome: StreamingOutcome,
    ctx: Context,
    ctx_req: ExecuteRequestContext,
    actor_id: str,
    actor_type: str,
) -> None:
    """Raise ``upstream.circuit_open`` for a streaming fast-fail, in its own transaction.

    The refusal that reaches here holds the breaker's per-host report claim,
    which is consumed on the request and good for one event per cooldown: every
    later refusal in that cooldown carries ``report=False``, so an event lost
    here is the operator's only notice for the whole cooldown. It therefore
    runs before the execution record and in a transaction of its own — neither
    a wait for a persist slot nor a failure writing the record can take it
    down — while still off the request, so the 503 never waits on the admin
    database. Its own failure is logged, never raised; a cancellation (the
    task's timeout, the shutdown drain) propagates to the supervisor.
    """
    try:
        async with ctx.admin_db.transaction() as session:
            await emit_circuit_open_event(
                session, ctx_req, actor_id=actor_id, actor_type=actor_type
            )
    except Exception:
        logger.error(
            "streaming_circuit_event_failed",
            execution_id=outcome.execution_id,
            trace_id=ctx_req.trace_id,
            exc_info=True,
        )


async def _persist_streaming_outcome(
    outcome: StreamingOutcome,
    ctx: Context,
    ctx_req: ExecuteRequestContext,
    started_at: datetime,
    actor_id: str,
    actor_type: str,
    origin: str | None = None,
    repeated_failure_counter: RepeatedFailureCounter | None = None,
) -> None:
    """Best-effort persistence of a streaming execution record.

    Extracted so unit tests can exercise the real logic without invoking the full
    handler. Runs off the request on the app's background-task registry, under
    ``broker.streaming_persist_timeout_s``. Concurrent calls for one Context are
    bounded by its ``_streaming_persist_gate``: an excess call waits for a slot
    before opening the admin transaction, for at most
    ``broker.streaming_persist_acquire_timeout_s``. Callers run it once the
    response is complete or the client has disconnected, when the request no
    longer holds its admission slot, so the wait delays neither the client nor
    ``/ready``. Never raises except ``CancelledError``: a write error, a slot
    timeout and a cancellation (its timeout or the shutdown drain) are each
    logged and counted on ``broker.streaming_execution.persist_failures`` by
    ``reason``.
    It runs after the streamed body has been sent, or after a call that ended
    before any response existed, so its write is never part of the response's
    ``Server-Timing`` field. An outcome with no ``http_status`` (the stream
    never opened, or was cancelled first) records as FAILED, whether or not it
    named the failure; a circuit fast-fail's ``upstream.circuit_open`` event is
    written first, ahead of the slot wait.
    """
    error_msg = outcome.error
    if error_msg is None and outcome.http_status is None:
        # The seam's invariant: an outcome with no upstream status names the
        # failure that ended the call before a response existed. An outcome
        # that breaks it still describes a call that reached no upstream, so it
        # records FAILED under a placeholder — never COMPLETED — and the breach
        # is logged where the injected broker that caused it can be found.
        logger.error(
            "streaming_outcome_missing_error",
            execution_id=outcome.execution_id,
            trace_id=ctx_req.trace_id,
        )
        error_msg = _MISSING_OUTCOME_ERROR
    status = (
        ExecutionStatus.FAILED
        if error_msg or (outcome.http_status is not None and outcome.http_status >= 400)
        else ExecutionStatus.COMPLETED
    )
    if status is ExecutionStatus.FAILED and error_msg is None:
        error_msg = f"Upstream returned {outcome.http_status}"
    if outcome.circuit_open:
        await _emit_streaming_circuit_event(outcome, ctx, ctx_req, actor_id, actor_type)
    try:
        semaphore = _streaming_persist_gate(ctx).semaphore
        acquire_timeout_s = ctx.config.broker.streaming_persist_acquire_timeout_s
    except Exception:
        _record_persist_failed(outcome, ctx_req)
        return
    if not await _acquire_persist_slot(semaphore, acquire_timeout_s, outcome, ctx_req):
        return
    _streaming_persist_pending.add(1, _PENDING_IN_FLIGHT)
    try:
        async with ctx.admin_db.transaction() as session:
            await persist_streaming_execution(
                session,
                execution_id=outcome.execution_id,
                started_at=started_at,
                status=status,
                http_status=outcome.http_status,
                duration_ms=outcome.duration_ms,
                error=error_msg,
                ctx_req=ctx_req,
                actor_id=actor_id,
                actor_type=actor_type,
                origin=origin,
                security_config=ctx.config.security,
                repeated_failure_counter=repeated_failure_counter,
                cancelled=outcome.cancelled,
            )
    except asyncio.CancelledError:
        _record_persist_abandoned(outcome, ctx_req, reason=_REASON_CANCELLED, stage="writing")
        raise
    except Exception:
        _record_persist_failed(outcome, ctx_req)
    finally:
        semaphore.release()
        _streaming_persist_pending.add(-1, _PENDING_IN_FLIGHT)
