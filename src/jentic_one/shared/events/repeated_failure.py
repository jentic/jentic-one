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

The scan is gated by :class:`RepeatedFailureCounter`, a cheap pre-filter on the
shared-state backend, so a failure that cannot reach the threshold costs no DB
statement at all; the database stays the authoritative confirmation. The gate
only ever skips the scan when the scan provably could not emit — see the class
docstring for the rule and its multi-instance caveat.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import structlog
from sqlalchemy import String, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.execution_records import ExecutionRecord
from jentic_one.shared.config import SecurityConfig
from jentic_one.shared.events import emit_event, summary_text
from jentic_one.shared.models import ExecutionStatus
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.schemas import OperationInfo
from jentic_one.shared.state import (
    AtomicStore,
    BackendKind,
    KeyValueStore,
    MemoryStateBackend,
    StateBackendConfig,
)

logger = structlog.get_logger(__name__)

_KEY_PREFIX = "repeated_failure"


class CounterStore(AtomicStore, KeyValueStore, Protocol):
    """The two shared-state roles the counter needs: atomic counters + TTL'd keys."""


@dataclass(frozen=True, slots=True)
class _AxisKey:
    """One aggregation key of the scan: actor + consumer axis + operation."""

    actor_id: str
    axis: str  # "toolkit" | "credential"
    axis_id: str
    operation_id: str

    def digest(self) -> str:
        # Hashed so arbitrary-length ids give a bounded, delimiter-safe key.
        raw = "\x1f".join((self.actor_id, self.axis, self.axis_id, self.operation_id))
        return hashlib.sha256(raw.encode()).hexdigest()[:32]


class RepeatedFailureCounter:
    """Sliding-window failure counter that gates the repeated-failure DB scan.

    **Counting.** Each failure increments a time bucket of width
    ``execution_repeated_failure_window_s`` (``floor(now / window)``) with an
    atomic ``incr_with_ttl`` that expires after two windows. The estimate for
    the scan's window ``[now - window, now]`` is *current + previous* bucket —
    that span always covers the window, so the estimate is an upper bound on the
    failures the scan can count. A record's ``started_at`` precedes its failure,
    so a record the scan counts was also counted here inside the covered span.

    **Gating rule.** The scan runs only when the estimate reaches
    ``execution_repeated_failure_threshold`` — below that the scan's own count is
    below it too and it could not emit. Above the threshold the scan is skipped
    only while every severity the scan could pick is known to be deduplicated:
    after a scan finds an existing event of severity *S*, a marker lives until
    that event leaves the dedup window (``created_at + window``); a CRITICAL
    event marks ERROR too, since an active CRITICAL deduplicates ERROR. The
    marker is set only from a *found* event (never from our own, possibly
    rolled-back, emit), so it never suppresses an emission the scan would make.
    ERROR is a possible pick whenever the critical threshold exceeds the base
    threshold; CRITICAL once the estimate reaches the critical threshold. The
    counter gives no lower bound on the scan's count (a counted failure's
    transaction may roll back, a run-time denial has no record, a long call's
    ``started_at`` can predate the window), so ERROR is never ruled out by
    count — with CRITICAL marking ERROR, a sustained CRITICAL outage keeps the
    gate closed regardless.

    **Keys.** The scan's toolkit axis counts every record with that toolkit and
    its credential axis every record with that credential (toolkit-path records
    included), so a failure increments the toolkit key *and* the credential key
    when it carries both; the gate reads the key the scan will use.

    **Multi-instance.** On Redis the counter is cluster-wide, so the rule holds
    across replicas (up to clock skew between instances at bucket edges, which
    already shifts the scan's own ``started_at`` window). On the ``memory``
    backend it is per process: a multi-replica deployment on ``memory`` — not a
    supported topology for the broker's shared state — would only scan once one
    replica alone sees ``threshold`` failures, delaying (or for a thin spread,
    missing) an emission the unconditional scan would make.

    **Failure.** Any backend error (Redis down, a bounded memory store full)
    fails open: the scan runs exactly as without a counter. An error can drop an
    increment, leaving the buckets short for as long as that failure could still
    be inside a window, so the process keeps scanning every failure for two
    windows after its last error (still counting, so the gate resumes warm). On
    Redis an increment dropped by one instance can still hide that failure from
    the others' gates for those two windows. The first error logs a warning;
    later ones log at debug so an outage doesn't flood the log.
    """

    def __init__(self, store: CounterStore, *, clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._clock = clock
        self._warned = False
        self._degraded_until = float("-inf")

    async def should_scan(
        self, key: _AxisKey, extra_keys: tuple[_AxisKey, ...], config: SecurityConfig
    ) -> bool:
        """Count one failure under *key* (+ *extra_keys*); True when the scan must run."""
        window = config.execution_repeated_failure_window_s
        now = self._clock()
        try:
            bucket = int(now // window)
            current = await self._store.incr_with_ttl(
                self._bucket_key(key, window, bucket), ttl_s=2 * window
            )
            for extra in extra_keys:
                await self._store.incr_with_ttl(
                    self._bucket_key(extra, window, bucket), ttl_s=2 * window
                )
            previous = await self._store.get(self._bucket_key(key, window, bucket - 1))
            estimate = current + (int(previous) if previous is not None else 0)
            if now < self._degraded_until:
                return True
            if estimate < config.execution_repeated_failure_threshold:
                return False
            for severity in _possible_severities(estimate, config):
                if await self._store.get(self._marker_key(key, window, severity)) is None:
                    return True
            return False
        except Exception:
            self._degraded_until = now + 2 * window
            self._log_unavailable(key)
            return True

    async def note_dedup_hit(
        self,
        key: _AxisKey,
        severity: EventSeverity,
        event_created_at: datetime,
        config: SecurityConfig,
    ) -> None:
        """Remember an existing event of *severity* until it leaves the dedup window.

        An active CRITICAL also deduplicates ERROR (see the dedup query), so a
        CRITICAL hit marks both severities; an ERROR hit never marks CRITICAL, so
        escalation stays immediate.
        """
        window = config.execution_repeated_failure_window_s
        created = (
            event_created_at
            if event_created_at.tzinfo is not None
            else event_created_at.replace(tzinfo=UTC)
        )
        ttl_s = created.timestamp() + window - self._clock()
        if ttl_s <= 0:
            return
        try:
            for covered in _DEDUP_COVERS[severity]:
                await self._store.set(self._marker_key(key, window, covered), b"1", ttl_s=ttl_s)
        except Exception:
            self._log_unavailable(key)

    def _log_unavailable(self, key: _AxisKey) -> None:
        log = logger.debug if self._warned else logger.warning
        self._warned = True
        log(
            "repeated_failure_counter_unavailable",
            actor_id=key.actor_id,
            operation_id=key.operation_id,
            exc_info=True,
        )

    @staticmethod
    def _bucket_key(key: _AxisKey, window: int, bucket: int) -> str:
        return f"{_KEY_PREFIX}:{window}:n:{bucket}:{key.digest()}"

    @staticmethod
    def _marker_key(key: _AxisKey, window: int, severity: EventSeverity) -> str:
        return f"{_KEY_PREFIX}:{window}:seen:{severity.value}:{key.digest()}"


# Which existing event severities deduplicate an emission of a given severity,
# and (the inverse) which markers an existing event of a given severity sets.
# CRITICAL subsumes ERROR; ERROR never suppresses escalation to CRITICAL.
_DEDUPED_BY: dict[EventSeverity, tuple[str, ...]] = {
    EventSeverity.ERROR: (EventSeverity.ERROR.value, EventSeverity.CRITICAL.value),
    EventSeverity.CRITICAL: (EventSeverity.CRITICAL.value,),
}
_DEDUP_COVERS: dict[EventSeverity, tuple[EventSeverity, ...]] = {
    EventSeverity.ERROR: (EventSeverity.ERROR,),
    EventSeverity.CRITICAL: (EventSeverity.CRITICAL, EventSeverity.ERROR),
}


def _utcnow() -> datetime:
    """Wall-clock "now" for the scan's window (a seam for timeline tests)."""
    return datetime.now(UTC)


def _possible_severities(estimate: int, config: SecurityConfig) -> list[EventSeverity]:
    """Severities the scan could pick given an upper bound on its count."""
    possible: list[EventSeverity] = []
    if config.execution_repeated_failure_threshold < (
        config.execution_repeated_failure_critical_threshold
    ):
        possible.append(EventSeverity.ERROR)
    if estimate >= config.execution_repeated_failure_critical_threshold:
        possible.append(EventSeverity.CRITICAL)
    return possible


def build_repeated_failure_counter(
    backend_config: StateBackendConfig,
    shared_backend: CounterStore,
    security_config: SecurityConfig,
) -> RepeatedFailureCounter:
    """Build the counter over the process's shared-state backend.

    Redis is shared as-is (bounded by its own TTLs and memory policy). On the
    ``memory`` backend the counter gets a dedicated store capped at
    ``execution_repeated_failure_counter_max_entries`` so a high-cardinality failure
    storm can neither grow without bound nor crowd out other shared state.
    """
    if backend_config.backend is BackendKind.REDIS:
        return RepeatedFailureCounter(shared_backend)
    return RepeatedFailureCounter(
        MemoryStateBackend(
            max_entries=security_config.execution_repeated_failure_counter_max_entries
        )
    )


async def maybe_emit_repeated_failure(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_type: str | None,
    toolkit_id: str | None,
    operation: OperationInfo | None,
    trace_id: str | None,
    config: SecurityConfig,
    credential_id: str | None = None,
    failure_counter: RepeatedFailureCounter | None = None,
) -> None:
    """Emit ``execution.repeated_failure`` when failures cross the threshold.

    Counts failed ``ExecutionRecord`` rows for the ``actor_id`` + consumer axis
    + operation-id key within ``execution_repeated_failure_window_s``. The
    aggregation key stays the opaque ``operation.id`` (the stable machine key
    on every record); the human-facing summary renders the operation's
    ``display`` label (method + path template) instead — the opaque hash only
    appears there for legacy in-flight jobs that carry no path. The
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
    threshold the pre-existing ERROR must not suppress the escalation, so a
    CRITICAL is deduplicated only by a CRITICAL. An active CRITICAL does
    deduplicate ERROR, though: while a CRITICAL for the key is in the window
    (the outage is at least that bad), no lower-severity ERROR is emitted.

    Best-effort: any failure (count query, dedup query, emit) is swallowed with a
    warning so the surrounding execution persistence is never disturbed.

    ``failure_counter`` gates the queries: when supplied, a failure the counter
    proves cannot emit issues no DB statement (see
    :class:`RepeatedFailureCounter`). ``None`` runs the queries on every failure.
    """
    # An aggregate key needs an operation plus a consumer axis to be meaningful;
    # with neither toolkit nor credential there is nothing to count.
    if operation is None or not operation.id or not (toolkit_id or credential_id):
        return
    operation_id = operation.id

    # The consumer axis: toolkit takes precedence (legacy path attribution);
    # the direct-binding path keys on the credential instead.
    if toolkit_id:
        record_axis = ExecutionRecord.toolkit_id == toolkit_id
        event_axis = Event.data["toolkit_id"].as_string() == toolkit_id
        axis_label = f"toolkit {toolkit_id}"
        axis_data: dict[str, str] = {"toolkit_id": toolkit_id}
        gate_key = _AxisKey(actor_id, "toolkit", toolkit_id, operation_id)
    else:
        record_axis = ExecutionRecord.credential_id == credential_id
        event_axis = Event.data["credential_id"].as_string() == credential_id
        axis_label = f"credential {credential_id}"
        axis_data = {"credential_id": credential_id or ""}
        gate_key = _AxisKey(actor_id, "credential", credential_id or "", operation_id)

    if failure_counter is not None:
        # A toolkit-path failure that also carries a credential is counted by the
        # credential-axis scan too, so it must feed that key as well.
        extra_keys = (
            (_AxisKey(actor_id, "credential", credential_id, operation_id),)
            if toolkit_id and credential_id
            else ()
        )
        if not await failure_counter.should_scan(gate_key, extra_keys, config):
            return

    try:
        # SAVEPOINT: on PostgreSQL a failed statement aborts the whole
        # transaction, so a bare try/except would swallow the error but leave the
        # caller's transaction poisoned — taking the already-flushed
        # ExecutionRecord down with it. Rolling back only the nested block keeps
        # the best-effort promise for DB-level failures too.
        async with session.begin_nested():
            now = _utcnow()
            window_start = now - timedelta(seconds=config.execution_repeated_failure_window_s)

            count_stmt = (
                select(func.count())
                .select_from(ExecutionRecord)
                .where(
                    # Rendered inline (not a bind parameter) so Postgres can match
                    # the ``WHERE status = 'failed'`` partial indexes even under a
                    # generic prepared-statement plan.
                    ExecutionRecord.status
                    == literal(ExecutionStatus.FAILED.value, String, literal_execute=True),
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

            # Per-key window dedup, severity-aware. A CRITICAL is deduplicated only
            # by a CRITICAL, so a later CRITICAL escalates past an ERROR emitted
            # earlier in the same window. An ERROR is deduplicated by an ERROR *or*
            # a CRITICAL: while a CRITICAL for the key is active in the window no
            # lower-severity ERROR is emitted for it. Match on the identifiers we
            # stamp into ``data`` so the check is exact.
            dedup_stmt = (
                select(Event.created_at, Event.severity)
                .where(
                    Event.type == EventType.EXECUTION_REPEATED_FAILURE,
                    Event.severity.in_(_DEDUPED_BY[severity]),
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
            existing = (await session.execute(dedup_stmt)).first()
            if existing is not None:
                if failure_counter is not None:
                    await failure_counter.note_dedup_hit(
                        gate_key, EventSeverity(existing[1]), existing[0], config
                    )
                return

            await emit_event(
                session,
                type=EventType.EXECUTION_REPEATED_FAILURE,
                severity=severity,
                summary=(
                    f"{failure_count} failures for operation "
                    f"{summary_text(operation.display)} "
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
                    # Structured human identity (nullable for legacy id-only
                    # jobs) so UI surfaces can render/filter the operation the
                    # same way as execution rows, without parsing the summary.
                    "operation_path": operation.path,
                    "operation_method": operation.method,
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
