"""Service-error to Problem Details mapping for the control web layer."""

from __future__ import annotations

from jentic_one.control.services.credentials.errors import (
    AgentBindingNotFoundError,
    CredentialNotFoundError,
    ImmutableFieldError,
    InvalidCredentialInputError,
    RuleSetAccessDeniedError,
    RuleSetInUseError,
    RuleSetNameConflictError,
    RuleSetNotFoundError,
    UnsupportedProviderForTypeError,
)
from jentic_one.shared.db.errors import (
    DatabaseDataError,
    DatabaseIntegrityError,
    DatabaseUnavailableError,
)
from jentic_one.shared.web.errors import make_service_error_handler

_ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    CredentialNotFoundError: (404, "credential_not_found"),
    ImmutableFieldError: (409, "immutable_field"),
    UnsupportedProviderForTypeError: (422, "unsupported_provider_for_type"),
    InvalidCredentialInputError: (400, "invalid_credential_input"),
    AgentBindingNotFoundError: (404, "agent_binding_not_found"),
    RuleSetNotFoundError: (404, "rule_set_not_found"),
    RuleSetNameConflictError: (409, "rule_set_name_conflict"),
    RuleSetInUseError: (409, "rule_set_in_use"),
    RuleSetAccessDeniedError: (403, "rule_set_access_denied"),
}

credential_service_error_handler = make_service_error_handler(_ERROR_MAP)


# A DB write failure that escapes a service unmapped is not a server fault: a
# constraint collision is a 409, a value too long for its column is a
# client-fixable 400 (#690), and a transient outage is a retryable 503. Map them
# to structured Problem Details instead of leaking a bare 500.
_DB_ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    DatabaseIntegrityError: (409, "conflict"),
    DatabaseDataError: (400, "invalid_input"),
    DatabaseUnavailableError: (503, "database_unavailable"),
}

# The wrapped errors carry the raw SQLAlchemy message — full SQL statement,
# bound parameters, and connection URL. Echoing that leaks internals (CWE-209),
# so the client gets a static, generic detail while the raw message is logged
# server-side (handled by the factory).
_DB_SAFE_DETAILS: dict[type[Exception], str] = {
    DatabaseIntegrityError: "The request conflicts with the current state of the resource.",
    DatabaseDataError: "A field value exceeds the maximum length allowed.",
    DatabaseUnavailableError: "The database is temporarily unavailable; please retry.",
}

database_error_handler = make_service_error_handler(_DB_ERROR_MAP, safe_details=_DB_SAFE_DETAILS)
