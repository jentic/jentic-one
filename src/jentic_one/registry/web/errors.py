"""Service-error to Problem Details mapping for the registry web layer."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from jentic_one.registry.services.errors import (
    AmbiguousMatchError,
    ApiNotFoundError,
    ArchivedRevisionPinError,
    CatalogEntryNotFoundError,
    CatalogUnavailableError,
    InvalidApiFilterError,
    InvalidNoteResourceError,
    InvalidOverlayDocumentError,
    MethodNotAllowedError,
    NoCurrentRevisionError,
    NoteNotFoundError,
    NotePreconditionFailedError,
    NothingToSnoozeError,
    OperationNotFoundError,
    OverlayApplyConflictError,
    OverlayNotFoundError,
    OverlayRollbackTargetMissingError,
    OverlayStateConflictError,
    OverlaySupersedeForbiddenError,
    RevisionNotFoundError,
    RevisionStateConflictError,
    SearchUnavailableError,
    SnoozeForbiddenError,
    SpecFileMissingError,
    TooManyCandidatesError,
)
from jentic_one.shared.db.errors import DatabaseConsistencyError
from jentic_one.shared.web.errors import make_service_error_handler

_ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    ApiNotFoundError: (404, "api_not_found"),
    RevisionNotFoundError: (404, "revision_not_found"),
    NoCurrentRevisionError: (404, "no_current_revision"),
    NoteNotFoundError: (404, "note_not_found"),
    OperationNotFoundError: (404, "operation_not_found"),
    OverlayNotFoundError: (404, "overlay_not_found"),
    CatalogEntryNotFoundError: (404, "catalog_entry_not_found"),
    MethodNotAllowedError: (405, "method_not_allowed"),
    AmbiguousMatchError: (409, "ambiguous_match"),
    # Refused overlay-superseding re-import (privilege inversion guard, A4b): the caller
    # lacks overlays:confirm. 403 (not 409) — it's an authorization decision, and an
    # operator-facing conflict event was re-emitted for someone who can resolve it.
    OverlaySupersedeForbiddenError: (403, "overlay_supersede_forbidden"),
    # Snooze/mute (C1): quieting a real upstream-drift notification is an operator
    # event-management action (events:write). A caller without it gets 403.
    SnoozeForbiddenError: (403, "snooze_forbidden"),
    OverlayStateConflictError: (409, "overlay_conflict"),
    OverlayApplyConflictError: (409, "overlay_apply_conflict"),
    # Snooze requested for an entry with no outstanding notified update — a precondition
    # failure (nothing to accept), same 409 family as the other lifecycle conflicts.
    NothingToSnoozeError: (409, "nothing_to_snooze"),
    # Rollback asked for a prior revision that was never recorded or is no longer
    # restorable — a precondition the operator must resolve (e.g. re-import upstream),
    # not a transient conflict. 409 keeps it in the same family as the other overlay
    # lifecycle conflicts while the distinct code lets surfaces message it precisely.
    OverlayRollbackTargetMissingError: (409, "overlay_rollback_target_missing"),
    NotePreconditionFailedError: (412, "precondition_failed"),
    InvalidApiFilterError: (422, "invalid_api_filter"),
    ArchivedRevisionPinError: (422, "archived_revision_pin"),
    InvalidNoteResourceError: (422, "invalid_note_resource"),
    InvalidOverlayDocumentError: (422, "invalid_overlay_document"),
    TooManyCandidatesError: (500, "url_index_overloaded"),
    RevisionStateConflictError: (409, "revision_state_conflict"),
    SearchUnavailableError: (501, "search_unsupported"),
    SpecFileMissingError: (500, "spec_file_missing"),
    CatalogUnavailableError: (502, "catalog_unavailable"),
    # Belt-and-braces: an accidental async lazy load (e.g. on a stale, bulk-updated
    # ORM instance) raises sqlalchemy MissingGreenlet, which the DB transaction
    # wrapper maps to DatabaseConsistencyError. Map it to a known 500 with a
    # generic client detail so it is logged as a recognised class instead of
    # escaping as an opaque traceback. See #642.
    DatabaseConsistencyError: (500, "internal_error"),
}

# Never surface raw SQLAlchemy internals (SQL, state, connection details) to the
# client for the defensively-mapped DatabaseConsistencyError; the raw message is
# still logged server-side (see make_service_error_handler).
_SAFE_DETAILS: dict[type[Exception], str] = {
    DatabaseConsistencyError: "An unexpected error occurred",
}


def _add_allow_header(
    request: Request, exc: Exception, status_code: int, response: JSONResponse
) -> JSONResponse:
    """Add Allow header for 405 responses when the exception carries allowed_methods."""
    if status_code == 405 and hasattr(exc, "allowed_methods"):
        response.headers["Allow"] = ", ".join(exc.allowed_methods)
    return response


service_error_handler = make_service_error_handler(
    _ERROR_MAP, response_hook=_add_allow_header, safe_details=_SAFE_DETAILS
)
