"""Service-error to Problem Details mapping for the auth web layer."""

from __future__ import annotations

from jentic_one.auth.services.errors import (
    ActorNotFoundError,
    AgentAlreadyOwnedError,
    ClaimTokenInvalidError,
    InvalidGrantError,
    InvalidOwnerError,
    InvalidTransitionError,
    NoApiKeyError,
    OperationNotSupportedError,
    RegistrationAccessDeniedError,
    ToolkitBindingConflictError,
    ToolkitBindingNotFoundError,
)
from jentic_one.shared.db.errors import DatabaseUnavailableError
from jentic_one.shared.web.errors import make_service_error_handler

_ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    ActorNotFoundError: (404, "actor_not_found"),
    AgentAlreadyOwnedError: (409, "agent_already_owned"),
    ClaimTokenInvalidError: (400, "invalid_claim_token"),
    InvalidGrantError: (400, "invalid_grant"),
    InvalidOwnerError: (422, "invalid_owner"),
    InvalidTransitionError: (409, "invalid_transition"),
    NoApiKeyError: (409, "no_api_key"),
    ToolkitBindingConflictError: (409, "toolkit_binding_conflict"),
    ToolkitBindingNotFoundError: (404, "toolkit_binding_not_found"),
    RegistrationAccessDeniedError: (401, "registration_access_denied"),
    OperationNotSupportedError: (403, "operation_not_supported"),
}

service_error_handler = make_service_error_handler(_ERROR_MAP)

# A transient DB failure that survives the in-transaction retry budget (e.g. a
# SQLite write-lock outlasting busy_timeout on the token-mint path) is infra,
# not a client fault: map it to a retryable 503 so CLIs/clients can back off,
# rather than a bare 500 that aborts `jentic bootstrap`.
_DB_ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    DatabaseUnavailableError: (503, "database_unavailable"),
}

# `DatabaseUnavailableError` wraps the raw SQLAlchemy `OperationalError`, whose
# `str()` carries the full SQL statement, every bound parameter, and the
# connection URL. Echoing that into the response body leaks internals to the
# caller (CWE-209), so send a static, generic detail and log the raw message
# server-side (handled by the factory).
_DB_SAFE_DETAILS: dict[type[Exception], str] = {
    DatabaseUnavailableError: "The database is temporarily unavailable; please retry.",
}

database_error_handler = make_service_error_handler(_DB_ERROR_MAP, safe_details=_DB_SAFE_DETAILS)
