"""What an agent's explanation can be about."""

from __future__ import annotations

from enum import StrEnum


class ExplanationSubject(StrEnum):
    """The kind of record an agent explanation is attached to."""

    #: A call an Ask rule held for a human decision (``held_…``).
    HELD_EXECUTION = "held_execution"
