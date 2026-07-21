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
    MethodNotAllowedError,
    NoCurrentRevisionError,
    NoteNotFoundError,
    NotePreconditionFailedError,
    OperationNotFoundError,
    OverlayNotFoundError,
    OverlayStateConflictError,
    RevisionNotFoundError,
    RevisionStateConflictError,
    SearchUnavailableError,
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
    OverlayStateConflictError: (409, "overlay_conflict"),
    NotePreconditionFailedError: (412, "precondition_failed"),
    InvalidApiFilterError: (422, "invalid_api_filter"),
    ArchivedRevisionPinError: (422, "archived_revision_pin"),
    InvalidNoteResourceError: (422, "invalid_note_resource"),
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
