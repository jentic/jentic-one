"""Ingest exception taxonomy."""


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


class DuplicateRevisionError(IngestStageError):
    """Raised when a revision with identical content already exists for the API."""

    _MESSAGE = (
        "A revision with identical content already exists for this API version "
        "(a previous import may have completed or failed midway). Re-import under "
        "a new version, or remove the existing revision"
    )

    def __init__(self) -> None:
        super().__init__(self._MESSAGE)


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
