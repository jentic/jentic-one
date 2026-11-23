"""Job handler protocol and registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from jentic_one.shared.db.protocols import TransactionalDatabase
from jentic_one.shared.models.jobs import JobKind


class NonRetryableJobError(Exception):
    """Raised by a handler when no later attempt can change the job's outcome.

    The worker moves the job to ``failed`` on the attempt that raised it, with
    ``str(exc)`` as ``job.error``, instead of requeueing it with backoff. Every
    other handler exception keeps the ``worker.max_attempts`` retry budget, so
    raise this only for a failure that is a pure function of the job's input
    (e.g. a payload or document that fails validation).
    """


@dataclass
class JobResultPayload:
    """Result produced by a job handler."""

    body: dict[str, Any]
    content_type: str | None = None


class JobHandler(Protocol):
    """Handler for a specific job kind."""

    async def execute(
        self,
        job_id: str,
        db: TransactionalDatabase,
        *,
        payload: dict[str, Any] | None = None,
        created_by: str | None = None,
        actor_type: str | None = None,
    ) -> JobResultPayload:
        """Run the job.

        ``db`` is the worker's ``DatabaseSession``. The worker opens no
        transaction around the handler: a handler opens its own short
        transactions on ``db``, so no connection (and, on SQLite, no write lock)
        is held across slow work such as an upstream call or a spec fetch.

        ``created_by``/``actor_type`` carry the enqueuing actor's id and kind.
        Handlers that write audit entries (which require a real actor) must
        reject a missing ``created_by``; handlers that only emit system
        lifecycle events may treat it as optional.

        Raising :class:`NonRetryableJobError` fails the job on this attempt;
        any other exception is retried with backoff.
        """
        ...


@dataclass
class JobHandlerRegistry:
    """Maps job kinds to their handler implementations."""

    _handlers: dict[JobKind, JobHandler] = field(default_factory=dict)

    @property
    def kinds(self) -> set[JobKind]:
        return set(self._handlers.keys())

    def register(self, kind: JobKind, handler: JobHandler) -> None:
        self._handlers[kind] = handler

    def get(self, kind: JobKind) -> JobHandler | None:
        return self._handlers.get(kind)
