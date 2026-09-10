"""Repeated-failure detection for the execution lifecycle.

Both the async-worker path (``shared/jobs/execution_handler.py``) and the
sync/streaming broker path (``broker/services/execution/service.py``) emit
``execution.failed`` on a failed call. When an actor's failures for a single
toolkit+operation cross a threshold within a rolling window this helper emits a
single ``execution.repeated_failure`` event (escalating to ``critical`` past a
second, higher threshold).

``ExecutionRecord`` rows live in the **admin** DB alongside ``events``, so both
the failure count and the dedup check are single-DB queries on the same session
the caller already holds. Like every other emitter the emit is best-effort: a
feed write must never break the execution it observes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.shared.config import SecurityConfig
from jentic_one.shared.events import emit_event
from jentic_one.shared.models import ExecutionStatus
from jentic_one.shared.models.events import EventSeverity, EventType

logger = structlog.get_logger(__name__)


async def maybe_emit_repeated_failure(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_type: str | None,
    toolkit_id: str | None,
    operation_id: str | None,
    trace_id: str | None,
    config: SecurityConfig,
    credential_id: str | None = None,
) -> None:
    """Emit ``execution.repeated_failure`` when failures cross the threshold.

    Counts failed ``ExecutionRecord`` rows for the ``actor_id`` + consumer axis
    + ``operation_id`` key within ``execution_repeated_failure_window_s``. The
    consumer axis is ``toolkit_id`` when present (the legacy toolkit path), else
    ``credential_id`` (the direct-binding path, theme-5 Phase 2 — whose
    executions carry no toolkit, so without this fallback the early return
    below would silently stop escalation for every direct-path caller). If the
    count reaches ``execution_repeated_failure_threshold`` it emits one event —
    ``CRITICAL`` once the count reaches
    ``execution_repeated_failure_critical_threshold``, else ``ERROR``.

    Dedup is **severity-aware**: at most one ERROR and one CRITICAL per key per
    window. As failures accumulate incrementally the count crosses the ERROR
    threshold first (one ERROR fires); when it later crosses the CRITICAL
    threshold the pre-existing ERROR must not suppress the escalation, so the
    dedup only looks for an event of the severity about to be emitted.

    Best-effort: any failure (count query, dedup query, emit) is swallowed with a
    warning so the surrounding execution persistence is never disturbed.
    """
    # An aggregate key needs an operation plus a consumer axis to be meaningful;
    # with neither toolkit nor credential there is nothing to count.
    if not operation_id or not (toolkit_id or credential_id):
        return

    # The consumer axis: toolkit takes precedence (legacy path attribution);
    # the direct-binding path keys on the credential instead.
    if toolkit_id:
        record_axis = ExecutionRecord.toolkit_id == toolkit_id
        event_axis = Event.data["toolkit_id"].as_string() == toolkit_id
        axis_label = f"toolkit {toolkit_id}"
        axis_data: dict[str, str] = {"toolkit_id": toolkit_id}
    else:
        record_axis = ExecutionRecord.credential_id == credential_id
        event_axis = Event.data["credential_id"].as_string() == credential_id
        axis_label = f"credential {credential_id}"
        axis_data = {"credential_id": credential_id or ""}

    try:
        now = datetime.now(UTC)
        window_start = now - timedelta(seconds=config.execution_repeated_failure_window_s)

        count_stmt = (
            select(func.count())
            .select_from(ExecutionRecord)
            .where(
                ExecutionRecord.status == ExecutionStatus.FAILED.value,
                ExecutionRecord.actor_id == actor_id,
                record_axis,
                ExecutionRecord.operation_id == operation_id,
                ExecutionRecord.started_at >= window_start,
            )
        )
        failure_count = int((await session.execute(count_stmt)).scalar_one())

        if failure_count < config.execution_repeated_failure_threshold:
            return

        is_critical = failure_count >= config.execution_repeated_failure_critical_threshold
        severity = EventSeverity.CRITICAL if is_critical else EventSeverity.ERROR

        # Per-key window dedup, severity-aware: at most one event of *this*
        # severity per key per window. Matching the severity (not just the type)
        # lets a later CRITICAL escalate past an ERROR already emitted earlier in
        # the same window as failures accumulate. Match on the identifiers we
        # stamp into ``data`` so the check is exact.
        dedup_stmt = (
            select(Event.id)
            .where(
                Event.type == EventType.EXECUTION_REPEATED_FAILURE,
                Event.severity == severity.value,
                Event.created_at >= window_start,
                Event.data["actor_id"].as_string() == actor_id,
                event_axis,
                Event.data["operation_id"].as_string() == operation_id,
            )
            .limit(1)
        )
        # Best-effort, unlocked read-then-write: two concurrent failed executions
        # for the same key can both pass this check and emit duplicate events. That
        # is acceptable for an ops signal (cf. the scanner's at-most-once caveat).
        if (await session.execute(dedup_stmt)).first() is not None:
            return

        await emit_event(
            session,
            type=EventType.EXECUTION_REPEATED_FAILURE,
            severity=severity,
            summary=(
                f"{failure_count} failures for operation {operation_id} "
                f"on {axis_label} in {config.execution_repeated_failure_window_s}s"
            ),
            requires_action=True,
            trace_id=trace_id,
            created_by=actor_id,
            actor_id=actor_id,
            actor_type=actor_type,
            data={
                "actor_id": actor_id,
                **axis_data,
                "operation_id": operation_id,
                "failure_count": failure_count,
                "window_s": config.execution_repeated_failure_window_s,
            },
        )
    except Exception:
        logger.warning(
            "emit_event_failed",
            event_type=EventType.EXECUTION_REPEATED_FAILURE,
            actor_id=actor_id,
            toolkit_id=toolkit_id,
            credential_id=credential_id,
            operation_id=operation_id,
        )
