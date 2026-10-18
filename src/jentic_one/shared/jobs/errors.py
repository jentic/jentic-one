"""Job-handler errors that steer the worker's retry decision.

Any other exception from a handler is retried with the worker's exponential
backoff until ``max_attempts``, then dead-lettered. A handler raises one of
these when it knows better:

- ``TerminalJobError``: retrying cannot succeed (the input itself is bad), so
  the job fails at once instead of burning its retry budget.
- ``RetryLaterJobError``: a transient failure whose cause said how long to
  wait; the requeue waits at least ``retry_after_s`` (still dead-lettered past
  the budget).
"""

from __future__ import annotations


class TerminalJobError(Exception):
    """A handler failure that no retry can fix; the job is failed immediately."""


class RetryLaterJobError(Exception):
    """A transient handler failure; requeue after at least ``retry_after_s``."""

    def __init__(self, message: str, *, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s
