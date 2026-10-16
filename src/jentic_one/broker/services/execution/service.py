"""Execution service — runs the shared pipeline and persists the result.

Services layer (00-overview): orchestrates the runner + persistence. The
transport is the ``HttpRunner`` (folded in); both the sync router and the
async worker call ``run_execution`` so they share one execution path, one
runner, and one persistence step. Status mirroring / header passthrough is the
caller's concern (the runner returns the verbatim upstream result).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import structlog
from opentelemetry import trace

from jentic_one.broker.adapters.runners.base import RunnerRequest, RunnerResult, UpstreamRunner
from jentic_one.broker.core.exceptions import BrokerError, CircuitOpenError
from jentic_one.broker.core.execution import execution_error, mint_execution_id
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.default_broker import DefaultBroker
from jentic_one.broker.services.execution.pipeline import (
    BrokerExecutionPipeline,
    ExecutionContext,
    ExecutionOutcome,
)
from jentic_one.shared.aws.sigv4 import SigV4Material
from jentic_one.shared.broker.broker import Broker
from jentic_one.shared.config import SecurityConfig
from jentic_one.shared.events import (
    MAX_EVENT_SUMMARY_FIELD_LEN,
    emit_event,
    valid_trace_id_or_none,
)
from jentic_one.shared.events.repeated_failure import maybe_emit_repeated_failure
from jentic_one.shared.executions import record_execution
from jentic_one.shared.metrics import get_meter
from jentic_one.shared.models import ExecutionStatus
from jentic_one.shared.models.actors import origin_or_none
from jentic_one.shared.models.events import ErrorSource, EventSeverity, EventTag, EventType
from jentic_one.shared.schemas import APIReference, OperationInfo
from jentic_one.shared.tracing import jentic_tracestate, pack_jentic_tracestate

logger = structlog.get_logger(__name__)
_tracer = trace.get_tracer("broker.execution")

_meter = get_meter("broker")
_executions_total = _meter.create_counter(
    "broker.executions.total",
    description="Total number of broker executions",
)
_execution_duration = _meter.create_histogram(
    "broker.execution.duration_ms",
    unit="ms",
    description="Duration of broker executions in milliseconds",
)

_circuit_event_last_emitted: dict[str, datetime] = {}

#: Upstream auth-rejection status → third-party ``auth_failure`` tag. 401 is an
#: RFC-tight authentication rejection; 403 mixes auth + authorization (kept as a
#: distinct, lower-precision tag so the funnel can separate them downstream).
_THIRDPARTY_AUTH_TAGS: dict[int, ErrorSource] = {
    401: ErrorSource.AUTH_THIRDPARTY_UNAUTHORIZED,
    403: ErrorSource.AUTH_THIRDPARTY_FORBIDDEN,
}


@dataclass(frozen=True, slots=True)
class UnmappedFailure:
    """A buffered execution that ended in an exception the broker does not map.

    The caller receives a 500 for it; this carries what its FAILED record needs.
    ``error`` is :func:`execution_error`'s ``server_error: <ExceptionClass>``.
    """

    execution_id: str
    started_at: datetime
    duration_ms: int
    error: str


#: Records an :class:`UnmappedFailure` without holding the error response: the
#: sync router's recorder hands the write to the app's background-task registry
#: and returns.
UnmappedFailureRecorder = Callable[[UnmappedFailure], Awaitable[None]]

#: Whether a non-``BrokerError`` exception reaches the caller as a 500. The sync
#: router answers from its app's exception handlers, so an exception a handler
#: maps to a 4xx or a 503 is never recorded as ``server_error``.
ServerErrorClassifier = Callable[[Exception], bool]


def _elapsed_ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def _should_emit_circuit_event(host: str, cooldown_s: int = 15) -> bool:
    """Return True if no circuit-open event was emitted for this host within the cooldown."""
    now = datetime.now(UTC)
    last = _circuit_event_last_emitted.get(host)
    if last is not None and (now - last).total_seconds() < cooldown_s:
        return False
    _circuit_event_last_emitted[host] = now
    stale = [
        h
        for h, ts in _circuit_event_last_emitted.items()
        if (now - ts).total_seconds() > cooldown_s
    ]
    for h in stale:
        _circuit_event_last_emitted.pop(h, None)
    return True


async def _emit_circuit_open_event(
    session: Any, ctx_req: ExecuteRequestContext, *, actor_id: str, actor_type: str
) -> None:
    """Emit the rate-limited ``upstream.circuit_open`` event for a circuit fast-fail.

    Shared by the buffered and streaming paths so either one raises the same
    operator-facing event, under the same per-host cooldown. A failed emit is
    logged, never raised.
    """
    host = urlparse(ctx_req.upstream_url).netloc or "<unknown>"
    if not _should_emit_circuit_event(host):
        return
    try:
        await emit_event(
            session,
            type=EventType.UPSTREAM_CIRCUIT_OPEN,
            severity=EventSeverity.WARNING,
            summary=f"Circuit breaker opened for upstream {host}",
            created_by=actor_id,
            actor_id=actor_id,
            actor_type=actor_type,
            data={"host": host},
        )
    except Exception:
        logger.warning("emit_circuit_event_failed", host=host)


def default_pipeline(runner: UpstreamRunner) -> BrokerExecutionPipeline:
    """Build the default pipeline around the given runner + default post stages.

    The runner is **required** (no implicit per-request ``HttpRunner()``): the
    upstream client is a single shared, lifespan-owned instance, so
    the caller builds an ``HttpRunner`` over the injected client and passes it in.
    """
    return BrokerExecutionPipeline(runner)


def default_broker(runner: UpstreamRunner) -> Broker:
    """Build the default :class:`Broker` for a runner.

    The per-request factory the surface + worker use when no ``Broker`` is
    injected via the ``AppContainer``. Wraps :func:`default_pipeline` in a
    :class:`DefaultBroker` so the execution path depends on the neutral ``Broker``
    seam; a caller swaps this factory to inject its own implementation.
    """
    return DefaultBroker(default_pipeline(runner))


def _api_reference(ctx_req: ExecuteRequestContext) -> APIReference | None:
    if not ctx_req.api_vendor:
        return None
    return APIReference(
        vendor=ctx_req.api_vendor,
        name=ctx_req.api_name or "",
        version=ctx_req.api_version or "",
    )


async def run_execution(
    ctx_req: ExecuteRequestContext,
    *,
    body: bytes | None,
    headers: dict[str, str] | None,
    session: Any,
    timeout: float = 30.0,
    broker: Broker,
    execution_id: str | None = None,
    actor_id: str,
    actor_type: str,
    origin: str | None = None,
    security_config: SecurityConfig | None = None,
    signing: SigV4Material | None = None,
    has_idempotency_key: bool = False,
    on_unmapped_failure: UnmappedFailureRecorder | None = None,
    is_server_error: ServerErrorClassifier | None = None,
) -> ExecutionOutcome:
    """Run the upstream call through the injected ``Broker`` and persist the record.

    On a transport-level failure the broker's pipeline raises a ``BrokerError``;
    we persist a FAILED record before re-raising so the central handler can map
    it to problem+json. The ``broker`` (and thus the shared upstream client it
    wraps) is supplied by the caller (one client per process); the default
    builds a :class:`DefaultBroker` per request, a caller may inject its own.

    An exception that is not a ``BrokerError`` and that ``is_server_error``
    says reaches the caller as a 500 (every such exception when it is
    ``None``) is counted as a FAILED execution and handed to
    ``on_unmapped_failure``, then re-raised unchanged. One the classifier
    rejects (an app handler maps it to a 4xx or a 503) is re-raised untouched:
    no count, no record. The sync router's recorder writes the FAILED record
    off the request, so the 500 never waits on the database; a recorder that
    raises is logged and never masks the cause. Without a recorder no record
    is written: the async worker reports the failure on the job result.

    ``execution_id`` lets the async worker reuse the id already handed to the
    client in the ``202`` (and used as the job's correlation id) so the persisted
    ``executions`` row matches; the sync router omits it and a fresh id is minted.

    ``has_idempotency_key`` tells the retry gate the caller sent an
    ``Idempotency-Key``; ``headers`` never carries it (it is broker-consumed).
    """
    execution_id = execution_id or mint_execution_id()
    started_at = datetime.now(UTC)
    t0 = time.perf_counter()
    operation_id = ctx_req.operation_id

    logger.info(
        "execution_started",
        execution_id=execution_id,
        actor_id=actor_id,
        operation_id=operation_id,
        api_vendor=ctx_req.api_vendor,
    )

    runner_request = RunnerRequest(
        method=ctx_req.method,
        url=ctx_req.upstream_url,
        headers=headers or {},
        body=body,
        timeout_s=timeout,
        signing=signing,
        has_idempotency_key=has_idempotency_key,
    )
    exec_context = ExecutionContext(
        execution_id=execution_id,
        toolkit_id=ctx_req.toolkit_id,
        operation=ctx_req.operation,
        api=_api_reference(ctx_req),
        trace_id=ctx_req.trace_id,
    )

    tracestate_member = pack_jentic_tracestate(
        execution_id=execution_id,
        toolkit_id=ctx_req.toolkit_id,
        vendor=ctx_req.api_vendor,
        name=ctx_req.api_name,
        version=ctx_req.api_version,
    )

    try:
        with _tracer.start_as_current_span("broker.execute") as span:
            span.set_attribute("execution_id", execution_id)
            span.set_attribute("operation_id", operation_id or "")
            span.set_attribute("toolkit_id", ctx_req.toolkit_id or "")
            span.set_attribute("api_vendor", ctx_req.api_vendor or "")
            with jentic_tracestate(tracestate_member):
                outcome = await broker.execute(runner_request, exec_context)
    except BrokerError as exc:
        error = execution_error(exc)
        duration_ms = _elapsed_ms(t0)
        logger.error(
            "execution_failed", execution_id=execution_id, error=error, detail=exc.detail[:128]
        )
        # No upstream response: counted as a FAILED execution, but no
        # duration sample — the streaming path applies the same policy.
        _executions_total.add(
            1, {"operation": operation_id or "", "status": ExecutionStatus.FAILED}
        )
        await _persist(
            ctx_req,
            session=session,
            execution_id=execution_id,
            started_at=started_at,
            status=ExecutionStatus.FAILED,
            http_status=None,
            duration_ms=duration_ms,
            error=error,
            actor_id=actor_id,
            actor_type=actor_type,
            origin=origin,
        )
        await _emit_execution_lifecycle(
            session,
            execution_id=execution_id,
            status=ExecutionStatus.FAILED,
            error_msg=error,
            trace_id=ctx_req.trace_id,
            actor_id=actor_id,
            actor_type=actor_type,
            toolkit_id=ctx_req.toolkit_id,
            credential_id=ctx_req.credential_id,
            operation=ctx_req.operation,
            security_config=security_config,
            origin=origin,
        )
        if isinstance(exc, CircuitOpenError):
            await _emit_circuit_open_event(
                session, ctx_req, actor_id=actor_id, actor_type=actor_type
            )
        # The sync router wraps this call in ``ctx.admin_db.transaction()``,
        # which rolls back on any exception. Commit the FAILED record + events
        # now so they survive the re-raise that maps the BrokerError to HTTP.
        await session.commit()  # arch-allow: manual-commit
        raise
    except Exception as exc:
        if is_server_error is not None and not is_server_error(exc):
            raise
        await _settle_unmapped_failure(
            exc,
            operation_id=operation_id,
            failure=UnmappedFailure(
                execution_id=execution_id,
                started_at=started_at,
                duration_ms=_elapsed_ms(t0),
                error=execution_error(exc),
            ),
            recorder=on_unmapped_failure,
        )
        raise

    result = outcome.result
    status = ExecutionStatus.FAILED if result.status_code >= 400 else ExecutionStatus.COMPLETED
    error_msg = (
        f"Upstream returned {result.status_code}" if status is ExecutionStatus.FAILED else None
    )

    duration_ms = int((time.perf_counter() - t0) * 1000)
    logger.info(
        "execution_finished",
        execution_id=execution_id,
        status=status,
        duration_ms=duration_ms,
    )

    _executions_total.add(1, {"operation": operation_id or "", "status": status})
    _execution_duration.record(result.duration_ms, {"operation": operation_id or ""})

    await _persist(
        ctx_req,
        session=session,
        execution_id=execution_id,
        started_at=started_at,
        status=status,
        http_status=result.status_code,
        duration_ms=result.duration_ms,
        error=error_msg,
        actor_id=actor_id,
        actor_type=actor_type,
        origin=origin,
    )

    # Third-party auth failure: the upstream rejected the auth the user
    # configured (401/403). Only when a credential path was attempted
    # (api_vendor set) — a vendorless call has no auth to reject. Tagged by
    # status so 401 (credential rejection) stays distinct from 403
    # (permission/business). We fold this into the EXECUTION_FAILED event as a
    # tag rather than emit a separate ``auth_failure``: the flat telemetry
    # payload carries no per-request correlation id, so two events sharing a
    # timestamp are indistinguishable from two concurrent requests downstream —
    # a separate event would permanently skew the funnel (see #446 review).
    thirdparty_tag = _THIRDPARTY_AUTH_TAGS.get(result.status_code)
    error_tags: set[EventTag] | None = (
        {thirdparty_tag} if thirdparty_tag is not None and ctx_req.api_vendor else None
    )

    await _emit_execution_lifecycle(
        session,
        execution_id=execution_id,
        status=status,
        error_msg=error_msg,
        trace_id=ctx_req.trace_id,
        actor_id=actor_id,
        actor_type=actor_type,
        toolkit_id=ctx_req.toolkit_id,
        credential_id=ctx_req.credential_id,
        operation=ctx_req.operation,
        security_config=security_config,
        error_tags=error_tags,
        origin=origin,
    )

    return outcome


async def _settle_unmapped_failure(
    exc: Exception,
    *,
    operation_id: str | None,
    failure: UnmappedFailure,
    recorder: UnmappedFailureRecorder | None,
) -> None:
    """Count and hand off an unmapped failure; never raises over ``exc``."""
    logger.error(
        "execution_failed",
        execution_id=failure.execution_id,
        error=failure.error,
    )
    # No upstream response: one FAILED execution, no duration sample — the
    # same policy as a BrokerError and the streaming path.
    _executions_total.add(1, {"operation": operation_id or "", "status": ExecutionStatus.FAILED})
    if recorder is None:
        return
    try:
        await recorder(failure)
    except Exception:
        logger.error(
            "unmapped_failure_record_failed",
            execution_id=failure.execution_id,
            error_type=type(exc).__name__,
            exc_info=True,
        )


async def persist_unmapped_failure(
    session: Any,
    failure: UnmappedFailure,
    *,
    ctx_req: ExecuteRequestContext,
    actor_id: str,
    actor_type: str,
    origin: str | None = None,
    security_config: SecurityConfig | None = None,
) -> None:
    """Write the FAILED record and lifecycle events for an :class:`UnmappedFailure`.

    The metric is already counted by :func:`run_execution` when it hands the
    failure off; this writes only the row (no upstream status) and the
    ``execution.failed`` event, as a ``BrokerError`` does.
    """
    await _persist(
        ctx_req,
        session=session,
        execution_id=failure.execution_id,
        started_at=failure.started_at,
        status=ExecutionStatus.FAILED,
        http_status=None,
        duration_ms=failure.duration_ms,
        error=failure.error,
        actor_id=actor_id,
        actor_type=actor_type,
        origin=origin,
    )
    await _emit_execution_lifecycle(
        session,
        execution_id=failure.execution_id,
        status=ExecutionStatus.FAILED,
        error_msg=failure.error,
        trace_id=ctx_req.trace_id,
        actor_id=actor_id,
        actor_type=actor_type,
        toolkit_id=ctx_req.toolkit_id,
        credential_id=ctx_req.credential_id,
        operation=ctx_req.operation,
        security_config=security_config,
        origin=origin,
    )


async def execute_upstream(
    ctx_req: ExecuteRequestContext,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    session: Any,
    timeout: float = 30.0,
    broker: Broker,
    actor_id: str,
    actor_type: str,
    origin: str | None = None,
    security_config: SecurityConfig | None = None,
) -> RunnerResult:
    """Run the broker and return only the upstream result (status/headers/body)."""
    outcome = await run_execution(
        ctx_req,
        body=body,
        headers=headers,
        session=session,
        timeout=timeout,
        broker=broker,
        actor_id=actor_id,
        actor_type=actor_type,
        origin=origin,
        security_config=security_config,
    )
    return outcome.result


async def persist_streaming_execution(
    session: Any,
    *,
    execution_id: str,
    started_at: datetime,
    status: ExecutionStatus,
    http_status: int | None,
    duration_ms: int,
    error: str | None,
    ctx_req: ExecuteRequestContext,
    actor_id: str,
    actor_type: str,
    origin: str | None = None,
    security_config: SecurityConfig | None = None,
    circuit_open: bool = False,
) -> None:
    """Persist an execution record for a streaming-path execution.

    Called once per streaming execution, on every outcome: after the response
    body ends (completed, failed mid-body, client disconnect), or when the
    stream ends before any response exists (it failed to open, or the request
    was cancelled first — ``http_status`` is ``None``). Metrics follow the
    buffered path: every execution counts on ``broker.executions.total``, and
    only one with an upstream response adds a duration sample. A
    ``circuit_open`` fast-fail also raises ``upstream.circuit_open``, under the
    buffered path's per-host cooldown.
    """
    operation_id = ctx_req.operation_id
    _executions_total.add(1, {"operation": operation_id or "", "status": status})
    if http_status is not None:
        _execution_duration.record(duration_ms, {"operation": operation_id or ""})

    logger.info(
        "execution_recorded",
        execution_id=execution_id,
        status=status,
        duration_ms=duration_ms,
        stream=True,
    )

    await record_execution(
        session,
        execution_id=execution_id,
        toolkit_id=ctx_req.toolkit_id,
        trace_id=ctx_req.trace_id,
        started_at=started_at,
        status=status,
        duration_ms=duration_ms,
        operation=ctx_req.operation,
        api_vendor=ctx_req.api_vendor,
        api_name=ctx_req.api_name,
        api_version=ctx_req.api_version,
        http_status=http_status,
        error=error,
        pinned_revisions=ctx_req.pinned_revisions,
        actor_id=actor_id,
        actor_type=actor_type,
        origin=origin,
        credential_id=ctx_req.credential_id,
        credential_name=ctx_req.credential_name,
    )

    # Third-party auth failure on the streaming path — mirrors run_execution
    # (see the comment there). The upstream status is the persisted ``http_status``
    # rather than a RunnerResult, but the seam is identical: a 401/403 with a
    # credential path attempted (api_vendor set) means the auth the user configured
    # was rejected upstream. Folded into EXECUTION_FAILED as a tag, not a separate
    # ``auth_failure`` event, to keep the correlation-id-free funnel un-skewed.
    thirdparty_tag = _THIRDPARTY_AUTH_TAGS.get(http_status) if http_status is not None else None
    error_tags: set[EventTag] | None = (
        {thirdparty_tag} if thirdparty_tag is not None and ctx_req.api_vendor else None
    )

    await _emit_execution_lifecycle(
        session,
        execution_id=execution_id,
        status=status,
        error_msg=error,
        trace_id=ctx_req.trace_id,
        actor_id=actor_id,
        actor_type=actor_type,
        toolkit_id=ctx_req.toolkit_id,
        credential_id=ctx_req.credential_id,
        operation=ctx_req.operation,
        security_config=security_config,
        error_tags=error_tags,
        origin=origin,
    )
    if circuit_open:
        await _emit_circuit_open_event(session, ctx_req, actor_id=actor_id, actor_type=actor_type)


async def _persist(
    ctx_req: ExecuteRequestContext,
    *,
    session: Any,
    execution_id: str,
    started_at: datetime,
    status: ExecutionStatus,
    http_status: int | None,
    duration_ms: int,
    error: str | None,
    actor_id: str,
    actor_type: str,
    origin: str | None = None,
) -> None:
    await record_execution(
        session,
        execution_id=execution_id,
        toolkit_id=ctx_req.toolkit_id,
        trace_id=ctx_req.trace_id,
        started_at=started_at,
        status=status,
        duration_ms=duration_ms,
        operation=ctx_req.operation,
        api_vendor=ctx_req.api_vendor,
        api_name=ctx_req.api_name,
        api_version=ctx_req.api_version,
        http_status=http_status,
        error=error,
        pinned_revisions=ctx_req.pinned_revisions,
        actor_id=actor_id,
        actor_type=actor_type,
        origin=origin,
        credential_id=ctx_req.credential_id,
        credential_name=ctx_req.credential_name,
    )


async def _emit_execution_lifecycle(
    session: Any,
    *,
    execution_id: str,
    status: ExecutionStatus,
    error_msg: str | None,
    trace_id: str | None,
    actor_id: str,
    actor_type: str,
    toolkit_id: str | None = None,
    credential_id: str | None = None,
    operation: OperationInfo | None = None,
    security_config: SecurityConfig | None = None,
    error_tags: set[EventTag] | None = None,
    origin: str | None = None,
) -> None:
    """Emit EXECUTION_COMPLETED/EXECUTION_FAILED events for the sync and streaming paths.

    On the FAILED branch this also drives ``execution.repeated_failure`` detection
    when ``security_config`` is supplied (the sync + streaming router paths pass it;
    the async-worker path detects repeated failures in its own handler).

    ``error_tags`` (FAILED branch only) rides along on the EXECUTION_FAILED event —
    e.g. an upstream 401/403 tags the failure with its third-party ``ErrorSource``
    so ``broker_execution_failed`` telemetry carries the auth split *without* a
    separate ``auth_failure`` event that the flat, correlation-id-free payload
    could never dedupe downstream.

    ``origin`` (the request-derived ``Origin`` string the callers already thread
    for the execution record) rides both events as a closed-enum ``Origin`` tag,
    so telemetry can split executions by surface (the MCP adoption metric)
    without any free-form property; an unrecognised value is simply not tagged.
    """
    event_trace_id = valid_trace_id_or_none(trace_id)
    origin_tag = origin_or_none(origin)
    try:
        if status == ExecutionStatus.COMPLETED:
            await emit_event(
                session,
                type=EventType.EXECUTION_COMPLETED,
                severity=EventSeverity.INFO,
                summary=f"Execution {execution_id} completed",
                execution_id=execution_id,
                trace_id=event_trace_id,
                created_by=actor_id,
                actor_id=actor_id,
                actor_type=actor_type,
                tags={origin_tag} if origin_tag is not None else None,
            )
        else:
            sanitized = (error_msg or "unknown")[:MAX_EVENT_SUMMARY_FIELD_LEN]
            failed_tags: set[EventTag] = set(error_tags or ())
            if origin_tag is not None:
                failed_tags.add(origin_tag)
            await emit_event(
                session,
                type=EventType.EXECUTION_FAILED,
                severity=EventSeverity.ERROR,
                summary=f"Execution failed: {sanitized}",
                requires_action=True,
                execution_id=execution_id,
                trace_id=event_trace_id,
                created_by=actor_id,
                actor_id=actor_id,
                actor_type=actor_type,
                tags=failed_tags or None,
            )
    except Exception:
        logger.warning("emit_execution_event_failed", execution_id=execution_id)

    if status == ExecutionStatus.FAILED and security_config is not None:
        await maybe_emit_repeated_failure(
            session,
            actor_id=actor_id,
            actor_type=actor_type,
            toolkit_id=toolkit_id,
            credential_id=credential_id,
            operation=operation,
            trace_id=event_trace_id,
            config=security_config,
        )
