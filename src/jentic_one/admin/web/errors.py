"""Service-error to Problem Details mapping for the admin web layer."""

from __future__ import annotations

from jentic_one.admin.services.errors import (
    AccountLockedError,
    AuditEntryNotFoundError,
    ConflictError,
    EmailAlreadyExistsError,
    EventNotFoundError,
    ExecutionNotFoundError,
    ExecutionRetentionElapsedError,
    InvalidCredentialsError,
    InvalidInputError,
    InviteTokenAlreadyRedeemedError,
    InviteTokenExpiredError,
    InviteTokenNotFoundError,
    JobNotCancellableError,
    JobNotCompletedError,
    JobNotFoundError,
    JobResultExpiredError,
    NotFoundError,
    OrgAdminGrantForbiddenError,
    PermissionNotGrantableError,
    SessionExpiredError,
    SetupAlreadyCompleteError,
    UnknownPermissionError,
    UserNotFoundError,
)
from jentic_one.shared.db.errors import DatabaseIntegrityError, DatabaseUnavailableError
from jentic_one.shared.pagination import InvalidCursorError
from jentic_one.shared.web.errors import make_service_error_handler

_ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    UserNotFoundError: (404, "user_not_found"),
    JobNotFoundError: (404, "job_not_found"),
    EventNotFoundError: (404, "event_not_found"),
    ExecutionRetentionElapsedError: (404, "execution_retention_elapsed"),
    ExecutionNotFoundError: (404, "execution_not_found"),
    AuditEntryNotFoundError: (404, "audit_entry_not_found"),
    InviteTokenNotFoundError: (404, "invite_token_not_found"),
    NotFoundError: (404, "not_found"),
    InvalidCredentialsError: (401, "invalid_credentials"),
    SessionExpiredError: (401, "session_expired"),
    AccountLockedError: (423, "account_locked"),
    EmailAlreadyExistsError: (409, "email_in_use"),
    InviteTokenAlreadyRedeemedError: (409, "invite_already_redeemed"),
    JobNotCancellableError: (409, "job_not_cancellable"),
    JobNotCompletedError: (409, "job_not_completed"),
    ConflictError: (409, "conflict"),
    InviteTokenExpiredError: (410, "invite_token_expired"),
    JobResultExpiredError: (410, "job_result_expired"),
    SetupAlreadyCompleteError: (410, "setup_already_complete"),
    UnknownPermissionError: (422, "unknown_permission"),
    PermissionNotGrantableError: (422, "permission_not_grantable"),
    OrgAdminGrantForbiddenError: (422, "org_admin_grant_forbidden"),
    InvalidInputError: (400, "invalid_input"),
    InvalidCursorError: (400, "invalid_cursor"),
}

service_error_handler = make_service_error_handler(_ERROR_MAP)

# A DB constraint violation that escapes a service unmapped (e.g. a unique-index
# collision the service didn't anticipate) is a conflict, not a server fault.
# Map it to a structured 409 Problem Detail so callers get a clean response
# instead of a bare 500. Services that have a more specific contract (e.g.
# bootstrap_admin → 410 setup_already_complete) still catch it first.
_DB_ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    DatabaseIntegrityError: (409, "conflict"),
    # A transient DB failure that outlasts the retry budget (e.g. a SQLite
    # write-lock) is infra, not a client fault — surface a retryable 503 so
    # callers can back off rather than seeing a bare 500.
    DatabaseUnavailableError: (503, "database_unavailable"),
}

# Both wrapped errors carry the raw SQLAlchemy exception message — full SQL
# statement, bound parameters, and connection URL. Echoing that into the
# response body leaks internals (CWE-209), so the client gets a static, generic
# detail while the raw message is logged server-side (handled by the factory).
_DB_SAFE_DETAILS: dict[type[Exception], str] = {
    DatabaseIntegrityError: "The request conflicts with the current state of the resource.",
    DatabaseUnavailableError: "The database is temporarily unavailable; please retry.",
}

database_error_handler = make_service_error_handler(_DB_ERROR_MAP, safe_details=_DB_SAFE_DETAILS)
