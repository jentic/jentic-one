"""Service-error to Problem Details mapping for the control web layer."""

from __future__ import annotations

import json

from fastapi import Request
from fastapi.responses import JSONResponse

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
from jentic_one.control.services.integrations.device_authorization import (
    DeviceAuthorizationError,
)
from jentic_one.control.services.integrations.errors import (
    AgentNotFoundError,
    ConfirmationForbiddenError,
    ConnectSessionServiceError,
    InvalidPollTokenError,
    InvalidStateTransitionError,
    NoOpForFlowError,
    ScopeValidationError,
    SessionNotFoundError,
)
from jentic_one.control.services.vendors.service import (
    UnknownVendorError,
    UnsupportedFlowError,
    VendorNotConfiguredError,
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


# Connect-session flow errors. The MRO walk maps subclasses first, so the
# ``ConnectSessionServiceError`` base is the safety net for unmapped
# subclasses (500 with a static detail — never ``str(exc)``, which could
# carry internals).
#
# ``InvalidPollTokenError`` is deliberately the only answer for both
# "session missing" and "token mismatch" on the poll_token-gated endpoints
# (review / confirm / status / cancel): session ids travel in approval
# URLs, so a 404-vs-403 split would be a session-id enumeration oracle.
_CONNECT_SESSION_ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    SessionNotFoundError: (404, "connect_session_not_found"),
    InvalidPollTokenError: (403, "invalid_poll_token"),
    InvalidStateTransitionError: (409, "connect_session_invalid_state"),
    ConfirmationForbiddenError: (403, "connect_session_confirmation_forbidden"),
    AgentNotFoundError: (400, "connect_session_agent_not_found"),
    ScopeValidationError: (400, "connect_session_unknown_scopes"),
    NoOpForFlowError: (400, "connect_session_unsupported_flow"),
    ConnectSessionServiceError: (500, "connect_session_error"),
}

_CONNECT_SESSION_SAFE_DETAILS: dict[type[Exception], str] = {
    ConnectSessionServiceError: "Internal error handling the connect session.",
}


def _connect_session_response_hook(
    request: Request, exc: Exception, status_code: int, response: JSONResponse
) -> JSONResponse:
    if isinstance(exc, ScopeValidationError):
        content: dict[str, object] = json.loads(bytes(response.body))
        content["unknown_scopes"] = exc.unknown
        return JSONResponse(
            status_code=status_code,
            content=content,
            media_type="application/problem+json",
        )
    return response


connect_session_error_handler = make_service_error_handler(
    _CONNECT_SESSION_ERROR_MAP,
    response_hook=_connect_session_response_hook,
    safe_details=_CONNECT_SESSION_SAFE_DETAILS,
)

# A vendor-side failure during ``:confirm`` (device-authorization ``begin``
# rejected by the vendor) is not a server fault and not permanent: the
# session is rolled back to ``created``, so the human can retry once the
# vendor recovers. 502 + ``retryable`` beats the raw 500 the human can't
# act on. Static detail — the exception message carries the vendor HTTP
# status, which is fine, but keeping the client detail static is one less
# thing to audit when the message evolves.
_DEVICE_AUTH_ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    DeviceAuthorizationError: (502, "vendor_upstream_error"),
}

_DEVICE_AUTH_SAFE_DETAILS: dict[type[Exception], str] = {
    DeviceAuthorizationError: (
        "The vendor rejected the authorization request; the session is still "
        "confirmable — retry once the vendor recovers"
    ),
}


def _device_auth_response_hook(
    request: Request, exc: Exception, status_code: int, response: JSONResponse
) -> JSONResponse:
    content: dict[str, object] = json.loads(bytes(response.body))
    content["retryable"] = True
    return JSONResponse(
        status_code=status_code,
        content=content,
        media_type="application/problem+json",
    )


device_authorization_error_handler = make_service_error_handler(
    _DEVICE_AUTH_ERROR_MAP,
    response_hook=_device_auth_response_hook,
    safe_details=_DEVICE_AUTH_SAFE_DETAILS,
)

_VENDOR_ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    UnknownVendorError: (404, "unknown_vendor"),
    UnsupportedFlowError: (400, "unsupported_flow"),
    VendorNotConfiguredError: (503, "vendor_not_configured"),
}

vendor_error_handler = make_service_error_handler(_VENDOR_ERROR_MAP)


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
