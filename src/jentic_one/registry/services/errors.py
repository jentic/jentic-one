"""Domain exception hierarchy for the registry module."""

from __future__ import annotations


class RegistryServiceError(Exception):
    """Base for all registry service errors."""


class ApiNotFoundError(RegistryServiceError):
    """Raised when an API identified by (vendor, name, version) does not exist."""

    def __init__(self, vendor: str, name: str, version: str) -> None:
        super().__init__(f"API '{vendor}/{name}/{version}' not found")
        self.vendor = vendor
        self.name = name
        self.version = version


class RevisionNotFoundError(RegistryServiceError):
    """Raised when a revision does not exist for a given API."""

    def __init__(self, revision_id: str, vendor: str, name: str, version: str) -> None:
        super().__init__(f"Revision '{revision_id}' not found for API '{vendor}/{name}/{version}'")
        self.revision_id = revision_id
        self.vendor = vendor
        self.name = name
        self.version = version


class NoCurrentRevisionError(RegistryServiceError):
    """Raised when an API has no current (live/published) revision."""

    def __init__(self, vendor: str, name: str, version: str) -> None:
        super().__init__(f"API '{vendor}/{name}/{version}' has no current (live) revision")
        self.vendor = vendor
        self.name = name
        self.version = version


class SpecFileMissingError(RegistryServiceError):
    """Raised when a revision exists but has no stored spec file (data integrity)."""

    def __init__(self, revision_id: str) -> None:
        super().__init__(f"Revision '{revision_id}' has no stored spec file")
        self.revision_id = revision_id


class OperationNotFoundError(RegistryServiceError):
    """Raised when an operation cannot be resolved by identifier."""

    def __init__(self, identifier: str) -> None:
        super().__init__(f"Operation '{identifier}' not found")
        self.identifier = identifier


class MethodNotAllowedError(RegistryServiceError):
    """Raised when the URL exists but not for the requested HTTP method."""

    def __init__(self, allowed_methods: list[str]) -> None:
        super().__init__(f"Method not allowed; allowed: {', '.join(allowed_methods)}")
        self.allowed_methods = allowed_methods


class AmbiguousMatchError(RegistryServiceError):
    """Raised when multiple URL index entries match with equal specificity."""

    def __init__(self, candidate_count: int) -> None:
        super().__init__(
            f"Ambiguous URL match: {candidate_count} candidates with equal specificity"
        )
        self.candidate_count = candidate_count


class TooManyCandidatesError(RegistryServiceError):
    """Raised when the URL index returns more candidates than can be ranked."""

    def __init__(self) -> None:
        super().__init__("URL index returned too many candidates")


class RevisionStateConflictError(RegistryServiceError):
    """Raised when a revision lifecycle action is invalid for the current state."""

    def __init__(
        self, revision_id: str, current_state: str, allowed_states: list[str], action: str
    ) -> None:
        super().__init__(
            f"Cannot {action} revision '{revision_id}': "
            f"state is '{current_state}', must be one of {allowed_states}"
        )
        self.revision_id = revision_id
        self.current_state = current_state
        self.allowed_states = allowed_states
        self.action = action


class OverlayNotFoundError(RegistryServiceError):
    """Raised when an overlay does not exist for a given API."""

    def __init__(self, overlay_id: str, vendor: str, name: str, version: str) -> None:
        super().__init__(f"Overlay '{overlay_id}' not found for API '{vendor}/{name}/{version}'")
        self.overlay_id = overlay_id
        self.vendor = vendor
        self.name = name
        self.version = version


class OverlayStateConflictError(RegistryServiceError):
    """Raised when an overlay lifecycle action is invalid for the current state."""

    def __init__(
        self, overlay_id: str, current_state: str, allowed_states: list[str], action: str
    ) -> None:
        super().__init__(
            f"Cannot {action} overlay '{overlay_id}': "
            f"state is '{current_state}', must be one of {allowed_states}"
        )
        self.overlay_id = overlay_id
        self.current_state = current_state
        self.allowed_states = allowed_states
        self.action = action


class OverlayApplyConflictError(RegistryServiceError):
    """Raised when a confirmed overlay cannot be materialized onto its base spec.

    Covers an overlay whose JSONPath targets no longer resolve against the base
    spec (drift), an unsupported target expression, or an overlaid spec that fails
    the post-apply safety checks (missing ``openapi`` key, unsafe ``servers[].url``).
    """

    def __init__(self, overlay_id: str, detail: str) -> None:
        super().__init__(f"Cannot materialize overlay '{overlay_id}': {detail}")
        self.overlay_id = overlay_id
        self.detail = detail


class InvalidOverlayDocumentError(RegistryServiceError):
    """Raised when an overlay document is structurally invalid or too large at submit.

    Rejects abusive input at the ingress (contributor with ``apis:write``) rather than
    only at confirm time: the document must be an object with a bounded ``actions``
    list and must not exceed the configured serialized-size cap.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(f"Invalid overlay document: {detail}")
        self.detail = detail


class SearchUnavailableError(RegistryServiceError):
    """Raised when search is not supported for the current backend/mode."""


class InvalidApiFilterError(RegistryServiceError):
    """Raised when an api filter identifier cannot be resolved."""

    def __init__(self, identifier: str) -> None:
        super().__init__(f"Unknown API filter: {identifier!r}")
        self.identifier = identifier


class ArchivedRevisionPinError(RegistryServiceError):
    """Raised when a revision_pin references an archived revision."""

    def __init__(self, api_identifier: str, revision_id: str) -> None:
        super().__init__(f"Cannot pin archived revision '{revision_id}' for API '{api_identifier}'")
        self.api_identifier = api_identifier
        self.revision_id = revision_id


class NoteNotFoundError(RegistryServiceError):
    """Raised when a note does not exist."""

    def __init__(self, note_id: str) -> None:
        super().__init__(f"Note '{note_id}' not found")
        self.note_id = note_id


class NotePreconditionFailedError(RegistryServiceError):
    """Raised when If-Match revision does not match the current note revision."""

    def __init__(self, note_id: str, expected: int, actual: int) -> None:
        super().__init__(
            f"Note '{note_id}' revision mismatch: expected {expected}, actual {actual}"
        )
        self.note_id = note_id
        self.expected = expected
        self.actual = actual


class InvalidNoteResourceError(RegistryServiceError):
    """Raised when note resource fields are invalid (zero or multiple specified)."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class CatalogEntryNotFoundError(RegistryServiceError):
    """Raised when a catalog entry cannot be resolved by api_id."""

    def __init__(self, api_id: str) -> None:
        super().__init__(f"Catalog entry '{api_id}' not found")
        self.api_id = api_id


class CatalogUnavailableError(RegistryServiceError):
    """Raised when the upstream catalog manifest/spec cannot be fetched or parsed."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail
