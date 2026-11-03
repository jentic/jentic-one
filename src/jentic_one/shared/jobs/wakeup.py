"""In-process wake-up signal from job enqueuers to the job worker.

The worker polls the jobs table on ``worker.poll_interval_s``. A job enqueued in
the **same process** (the broker's ``Prefer: respond-async`` path, a registry
import) does not need to wait out that interval: :func:`notify_on_commit` arms a
one-shot signal that fires once the enqueuing transaction commits, and every
:class:`~jentic_one.shared.jobs.worker.WorkerLoop` running in the process polls
immediately.

The signal is a latency hint, never a correctness mechanism: a lost or spurious
wake-up only changes *when* the next poll happens, and jobs enqueued by another
process are still picked up by the regular poll. Firing only after commit (not
at flush) keeps the woken poll from racing the enqueuing transaction and
finding nothing to claim.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from sqlalchemy import event

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

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


def notify_on_commit(session: AsyncSession, wakeup: JobWakeup | None = None) -> None:
    """Wake in-process workers once ``session``'s current transaction commits.

    A rolled-back transaction fires nothing for the rolled-back work; the armed
    listener then fires on the session's next commit instead, which at worst
    costs one empty poll.
    """
    target = wakeup or job_wakeup
    sync_session = session.sync_session
    if sync_session.info.get(_ARMED_KEY):
        return
    sync_session.info[_ARMED_KEY] = True

    def _after_commit(committed: Any) -> None:
        committed.info.pop(_ARMED_KEY, None)
        target.notify()

    event.listen(sync_session, "after_commit", _after_commit, once=True)
