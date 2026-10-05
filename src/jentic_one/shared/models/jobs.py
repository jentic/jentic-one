"""Job-related enums shared across modules."""

from enum import StrEnum


class JobStatus(StrEnum):
    """Status of a job through its lifecycle."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    # Exhausted its retry budget after repeated handler failures; parked here
    # (poison-message handling) instead of looping forever.
    DEAD_LETTER = "dead_letter"
    # Enqueued but not yet claimable — awaiting human approval. The worker
    # skips held jobs; the approval surface flips them to QUEUED on approval
    # or FAILED on denial / expiry.
    HELD = "held"


class JobKind(StrEnum):
    """Kind of job to be executed."""

    IMPORT = "import"
    EXECUTION = "execution"
