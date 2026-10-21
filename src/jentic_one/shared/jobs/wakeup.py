"""In-process and cross-process wake-up signals from job enqueuers to workers.

The worker polls the jobs table on ``worker.poll_interval_s``. A job enqueued in
the **same process** (the broker's ``Prefer: respond-async`` path, a registry
import, an approval release) does not need to wait out that interval:
:func:`notify_on_commit` arms a one-shot signal that fires once the enqueuing
transaction commits, and every :class:`~jentic_one.shared.jobs.worker.WorkerLoop`
running in the process polls immediately.

On PostgreSQL, :func:`notify_job_enqueued` also issues
``SELECT pg_notify('jentic_jobs', :payload)`` inside the enqueuing transaction.
PostgreSQL delivers transactional notifications only when the transaction
commits, waking worker loops in other processes (multiple uvicorn workers,
broker replicas, or split deployments) that hold a ``LISTEN jentic_jobs``
connection. The payload carries the job ``kind`` so a worker whose registered
handlers do not include that kind skips the poll.

Both signals are latency hints, never correctness mechanisms: a lost or spurious
wake-up only changes *when* the next poll happens, and regular polling remains
the fallback on reconnects, behind transaction-mode connection poolers, and on
SQLite.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from sqlalchemy import event, text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

#: Postgres LISTEN/NOTIFY channel used to wake worker loops across processes.
JOB_NOTIFY_CHANNEL = "jentic_jobs"

_NOTIFY_SQL = text(f"SELECT pg_notify('{JOB_NOTIFY_CHANNEL}', :payload)")

# Session.info key marking a session that already has a pending wake-up armed,
# so enqueuing several jobs in one transaction registers one listener.
_ARMED_KEY = "jentic_job_wakeup_armed"


class JobWakeup:
    """Fan-out of a "jobs were enqueued" signal to subscribed worker events.

    Subscribers are ``asyncio.Event`` objects owned by worker loops. Each is
    recorded with the event loop it was subscribed from and set via
    ``call_soon_threadsafe``, so a notify from any thread or loop is safe.
    """

    def __init__(self) -> None:
        self._subscribers: dict[asyncio.Event, asyncio.AbstractEventLoop] = {}

    def subscribe(self, wake: asyncio.Event) -> None:
        """Register ``wake`` (owned by the running event loop) for notifications."""
        self._subscribers[wake] = asyncio.get_running_loop()

    def unsubscribe(self, wake: asyncio.Event) -> None:
        """Stop notifying ``wake``; unknown events are ignored."""
        self._subscribers.pop(wake, None)

    def notify(self) -> None:
        """Set every subscribed event (best-effort; never raises).

        Runs inside a SQLAlchemy ``after_commit`` listener, whose exceptions
        surface from ``commit()``: a failure here would fail a request whose
        job is already committed. A subscriber whose loop is closed — checked
        up front, or closing between that check and the call — is dropped.
        """
        for wake, loop in list(self._subscribers.items()):
            if loop.is_closed():
                self._subscribers.pop(wake, None)
                continue
            try:
                loop.call_soon_threadsafe(wake.set)
            except RuntimeError:
                self._subscribers.pop(wake, None)


# Process-wide instance: enqueuers and worker loops in one process meet here.
job_wakeup = JobWakeup()


def _is_postgres_session(session: AsyncSession) -> bool:
    """True when ``session`` is bound to a PostgreSQL engine."""
    bind = getattr(session, "bind", None)
    if bind is None and hasattr(session, "get_bind"):
        with_bind = session.get_bind
        try:
            bind = with_bind()
        except Exception:
            bind = None
    dialect = getattr(bind, "dialect", None)
    return getattr(dialect, "name", None) == "postgresql"


def notify_on_commit(session: AsyncSession, wakeup: JobWakeup | None = None) -> None:
    """Wake in-process workers once ``session``'s current transaction commits.

    A rolled-back transaction fires nothing for the rolled-back work; the armed
    listener then fires on the session's next commit instead, which at worst
    costs one empty poll.
    """
    target = wakeup or job_wakeup
    sync_session = getattr(session, "sync_session", None)
    if sync_session is None:
        return
    if sync_session.info.get(_ARMED_KEY):
        return
    sync_session.info[_ARMED_KEY] = True

    def _after_commit(committed: Any) -> None:
        committed.info.pop(_ARMED_KEY, None)
        target.notify()

    event.listen(sync_session, "after_commit", _after_commit, once=True)


async def notify_job_enqueued(
    session: AsyncSession,
    kind: str = "",
    *,
    wakeup: JobWakeup | None = None,
) -> None:
    """Wake in-process and cross-process workers once ``session`` commits.

    Arms the in-process :func:`notify_on_commit` hook and, on PostgreSQL,
    executes ``SELECT pg_notify('jentic_jobs', :payload)`` in the caller's
    transaction so PostgreSQL delivers the notification to every listening
    worker process only when the transaction commits. ``kind`` (the enqueued
    :class:`~jentic_one.shared.models.jobs.JobKind`, or ``""`` when unspecified)
    is sent as the notification payload so worker loops whose registered
    handlers do not include that kind ignore the notification.
    """
    notify_on_commit(session, wakeup)
    if not _is_postgres_session(session):
        return
    await session.execute(
        _NOTIFY_SQL,
        {"payload": str(kind) if kind else ""},
    )
