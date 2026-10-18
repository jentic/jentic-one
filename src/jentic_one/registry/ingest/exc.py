"""Ingest exception taxonomy."""

from jentic_one.shared.jobs.errors import RetryLaterJobError, TerminalJobError


class BaseIngestError(Exception):
    """Base for all ingest errors."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class IngestPipelineError(BaseIngestError):
    """Raised when the ingest pipeline encounters a fatal error."""


class IngestStageError(BaseIngestError):
    """Raised when a pipeline stage fails."""


class IngestJobError(BaseIngestError):
    """Raised when an ingest job fails."""


class TerminalIngestJobError(IngestJobError, TerminalJobError):
    """An ingest job failure no retry can fix (the worker fails it at once)."""


class RetryLaterIngestJobError(IngestJobError, RetryLaterJobError):
    """A transient ingest job failure with a known wait (requeued after it)."""

    def __init__(self, message: str, *, retry_after_s: float | None = None) -> None:
        IngestJobError.__init__(self, message)
        self.retry_after_s = retry_after_s


class CatalogSpecUnavailableIngestError(IngestStageError):
    """A catalog entry's spec can't be fetched, and retrying won't change that.

    The entry is no longer listed by an enabled source, lists no spec, or its
    source says the spec is gone. The import job fails without retrying.
    """


class CatalogSourceBusyIngestError(IngestStageError):
    """A catalog entry's source is throttled, down or timed out (transient).

    ``retry_after_s`` is the wait the source asked for, when it gave one; the
    import job is requeued no sooner than that.
    """

    def __init__(self, message: str, *, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class CatalogIdentityConflictError(IngestStageError):
    """Raised when a catalog import collides with a different catalog entry's identity.

    Two distinct catalog entries can collapse to the same registry identity
    (vendor extraction reduces hosts to eTLD+1); stacking the second entry's
    spec onto the first's Api row as a new revision would silently corrupt
    provenance, so the import is refused instead (#1020).
    """

    def __init__(
        self, *, vendor: str, name: str, version: str, stored_id: str, incoming_id: str
    ) -> None:
        super().__init__(
            f"catalog identity conflict: '{vendor}/{name}' ({version}) is already "
            f"imported from catalog entry '{stored_id}', which collides with "
            f"'{incoming_id}' on the same registry identity; the existing import is "
            f"unchanged — delete API '{vendor}/{name}' first if you meant to replace it"
        )


class DuplicateRevisionError(IngestStageError):
    """Raised when a revision with identical content already exists for the API."""

    _MESSAGE = (
        "A revision with identical content already exists for this API version "
        "(a previous import may have completed or failed midway). Re-import under "
        "a new version, or remove the existing revision"
    )

    def __init__(self) -> None:
        super().__init__(self._MESSAGE)


class HostOwnedByOtherVendorIngestError(IngestStageError):
    """Raised when an auto-live import declares a host another vendor's live API serves.

    Imports that go live immediately (``origin`` set, e.g. catalog or overlay
    materialization) are held to the same one-vendor-per-host rule as promotion.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(f"Cannot import this spec as live: {detail}")


class MissingRequiredKeysError(IngestStageError):
    """Raised when required keys are missing from stage context."""

    def __init__(self, missing_keys: set[str], stage: object | None = None) -> None:
        stage_name = stage.name if hasattr(stage, "name") else "Unknown"
        message = f"Stage '{stage_name}' is missing required keys: {sorted(missing_keys)}"
        super().__init__(message)
        self.missing_keys = missing_keys
        self.stage = stage


class MissingProducedKeyError(IngestStageError):
    """Raised when a stage fails to produce an expected key."""

    def __init__(self, missing_key: str, stage: object | None = None) -> None:
        stage_name = stage.name if hasattr(stage, "name") else "Unknown"
        message = f"Stage '{stage_name}' did not produce expected key: '{missing_key}'"
        super().__init__(message)
        self.missing_key = missing_key
        self.stage = stage


class BaseWrongTypeError(IngestStageError):
    """Base for type-mismatch errors in stage context."""

    error_string: str = "wrong type"

    def __init__(
        self,
        key: str,
        expected_type: type,
        actual_type: type,
        stage: object | None = None,
    ) -> None:
        stage_name = stage.name if hasattr(stage, "name") else "Unknown"
        message = (
            f"Stage '{stage_name}' {self.error_string} for key '{key}': "
            f"expected {expected_type.__name__}, got {actual_type.__name__}"
        )
        super().__init__(message)
        self.key = key
        self.expected_type = expected_type
        self.actual_type = actual_type
        self.stage = stage


class WrongTypeRequiredError(BaseWrongTypeError):
    """Raised when a required key has the wrong type."""

    error_string: str = "has wrong type for required key"


class WrongTypeProducedError(BaseWrongTypeError):
    """Raised when a produced key has the wrong type."""

    error_string: str = "produced wrong type for key"
