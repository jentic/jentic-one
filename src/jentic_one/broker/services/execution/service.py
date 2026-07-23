"""Execution service — runs the shared pipeline and persists the result.

Services layer (00-overview): orchestrates the runner + persistence. The
transport is the RN-0 ``HttpRunner`` (folded in); both the sync router and the
async worker call ``run_execution`` so they share one execution path, one
runner, and one persistence step. Status mirroring / header passthrough is the
caller's concern (the runner returns the verbatim upstream result).
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import structlog
from opentelemetry import trace

from jentic_one.broker.adapters.runners.base import RunnerRequest, RunnerResult, UpstreamRunner
from jentic_one.broker.core.exceptions import BrokerError, CircuitOpenError
from jentic_one.broker.core.execution import mint_execution_id
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.default_broker import DefaultBroker
from jentic_one.broker.services.execution.pipeline import (
    BrokerExecutionPipeline,
    ExecutionContext,
    ExecutionOutcome,
)
from jentic_one.shared.broker.broker import Broker
from jentic_one.shared.config import SecurityConfig
from jentic_one.shared.events import emit_event
from jentic_one.shared.events.repeated_failure import maybe_emit_repeated_failure
from jentic_one.shared.executions import record_execution
from jentic_one.shared.metrics import get_meter
from jentic_one.shared.models import ExecutionStatus
from jentic_one.shared.models.events import ErrorSource, EventSeverity, EventTag, EventType
from jentic_one.shared.schemas import APIReference
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
_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_MAX_EVENT_SUMMARY_LEN = 128

#: Upstream auth-rejection status → third-party ``auth_failure`` tag. 401 is an
#: RFC-tight authentication rejection; 403 mixes auth + authorization (kept as a
#: distinct, lower-precision tag so the funnel can separate them downstream).
_THIRDPARTY_AUTH_TAGS: dict[int, ErrorSource] = {
    401: ErrorSource.AUTH_THIRDPARTY_UNAUTHORIZED,
    403: ErrorSource.AUTH_THIRDPARTY_FORBIDDEN,
}


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


def default_pipeline(runner: UpstreamRunner) -> BrokerExecutionPipeline:
    """Build the Phase-1 pipeline around the given runner + default post stages.

    The runner is **required** (no implicit per-request ``HttpRunner()``): §04
    (PR-B) made the upstream client a single shared, lifespan-owned instance, so
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
) -> ExecutionOutcome:
    """Run the upstream call through the injected ``Broker`` and persist the record.

    On a transport-level failure the broker's pipeline raises a ``BrokerError``;
    we persist a FAILED record before re-raising so the central handler can map
    it to problem+json. The ``broker`` (and thus the shared upstream client it
    wraps) is supplied by the caller (§04 — one client per process); the default
    builds a :class:`DefaultBroker` per request, a caller may inject its own.

    ``execution_id`` lets the async worker reuse the id already handed to the
    client in the ``202`` (and used as the job's correlation id) so the persisted
    ``executions`` row matches; the sync router omits it and a fresh id is minted.
    """
    execution_id = execution_id or mint_execution_id()
    started_at = datetime.now(UTC)
    t0 = time.perf_counter()

    logger.info(
        "execution_started",
        execution_id=execution_id,
        actor_id=actor_id,
        operation_id=ctx_req.operation_id,
        api_vendor=ctx_req.api_vendor,
    )

    runner_request = RunnerRequest(
        method=ctx_req.method,
        url=ctx_req.upstream_url,
        headers=headers or {},
        body=body,
        timeout_s=timeout,
    )
    exec_context = ExecutionContext(
        execution_id=execution_id,
        toolkit_id=ctx_req.toolkit_id,
        operation_id=ctx_req.operation_id,
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
            span.set_attribute("operation_id", ctx_req.operation_id or "")
            span.set_attribute("toolkit_id", ctx_req.toolkit_id or "")
            span.set_attribute("api_vendor", ctx_req.api_vendor or "")
            with jentic_tracestate(tracestate_member):
                outcome = await broker.execute(runner_request, exec_context)
    except BrokerError as exc:
        logger.error("execution_failed", execution_id=execution_id, error=exc.detail[:128])
        await _persist(
            ctx_req,
            session=session,
            execution_id=execution_id,
            started_at=started_at,
            status=ExecutionStatus.FAILED,
            http_status=None,
            duration_ms=0,
            error=exc.detail[:128],
            actor_id=actor_id,
            actor_type=actor_type,
            origin=origin,
        )
        await _emit_execution_lifecycle(
            session,
            execution_id=execution_id,
            status=ExecutionStatus.FAILED,
            error_msg=exc.detail[:128],
            trace_id=ctx_req.trace_id,
            actor_id=actor_id,
            actor_type=actor_type,
            toolkit_id=ctx_req.toolkit_id,
            operation_id=ctx_req.operation_id,
            security_config=security_config,
        )
        if isinstance(exc, CircuitOpenError):
            host = urlparse(ctx_req.upstream_url).netloc or "<unknown>"
            if _should_emit_circuit_event(host):
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
        # The sync router wraps this call in ``ctx.admin_db.transaction()``,
        # which rolls back on any exception. Commit the FAILED record + events
        # now so they survive the re-raise that maps the BrokerError to HTTP.
        await session.commit()  # arch-allow: manual-commit
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

    _executions_total.add(1, {"operation": ctx_req.operation_id or "", "status": status})
    _execution_duration.record(result.duration_ms, {"operation": ctx_req.operation_id or ""})

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
        operation_id=ctx_req.operation_id,
        security_config=security_config,
        error_tags=error_tags,
    )

    return outcome


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
) -> None:
    """Persist an execution record for a streaming-path execution.

    Called from the background task after the streaming response body completes
    (or terminates with an error). Shares metrics instrumentation with the
    buffered path for observability parity.
    """
    operation = ctx_req.operation_id or ""
    _executions_total.add(1, {"operation": operation, "status": status})
    _execution_duration.record(duration_ms, {"operation": operation})

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
        toolkit_id=ctx_req.toolkit_id or "",
        trace_id=ctx_req.trace_id,
        started_at=started_at,
        status=status,
        duration_ms=duration_ms,
        operation_id=ctx_req.operation_id,
        api_vendor=ctx_req.api_vendor,
        api_name=ctx_req.api_name,
        api_version=ctx_req.api_version,
        http_status=http_status,
        error=error,
        pinned_revisions=ctx_req.pinned_revisions,
        actor_id=actor_id,
        actor_type=actor_type,
        origin=origin,
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
        operation_id=ctx_req.operation_id,
        security_config=security_config,
        error_tags=error_tags,
    )


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
        toolkit_id=ctx_req.toolkit_id or "",
        trace_id=ctx_req.trace_id,
        started_at=started_at,
        status=status,
        duration_ms=duration_ms,
        operation_id=ctx_req.operation_id,
        api_vendor=ctx_req.api_vendor,
        api_name=ctx_req.api_name,
        api_version=ctx_req.api_version,
        http_status=http_status,
        error=error,
        pinned_revisions=ctx_req.pinned_revisions,
        actor_id=actor_id,
        actor_type=actor_type,
        origin=origin,
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
    operation_id: str | None = None,
    security_config: SecurityConfig | None = None,
    error_tags: set[EventTag] | None = None,
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
    """
    event_trace_id = trace_id if trace_id and _TRACE_ID_RE.match(trace_id) else None
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
            )
        else:
            sanitized = (error_msg or "unknown")[:_MAX_EVENT_SUMMARY_LEN]
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
                tags=error_tags or None,
            )
    except Exception:
        logger.warning("emit_execution_event_failed", execution_id=execution_id)

    if status == ExecutionStatus.FAILED and security_config is not None:
        await maybe_emit_repeated_failure(
            session,
            actor_id=actor_id,
            actor_type=actor_type,
            toolkit_id=toolkit_id,
            operation_id=operation_id,
            trace_id=event_trace_id,
            config=security_config,
        )
