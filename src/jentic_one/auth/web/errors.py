"""Service-error to Problem Details mapping for the auth web layer."""

from __future__ import annotations

import math

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse

from jentic_one.auth.services.errors import (
    ActorNotFoundError,
    AgentAlreadyOwnedError,
    ClaimActorNotAllowedError,
    ClaimTokenInvalidError,
    InvalidClientMetadataError,
    InvalidGrantError,
    InvalidOwnerError,
    InvalidRevocationRequestError,
    InvalidTransitionError,
    NoApiKeyError,
    OAuthGrantAccessDeniedError,
    OAuthGrantNotFoundError,
    OperationNotSupportedError,
    RateLimitExceededError,
    RegistrationAccessDeniedError,
    ToolkitBindingConflictError,
    ToolkitBindingNotFoundError,
)
from jentic_one.shared.db.errors import DatabaseUnavailableError
from jentic_one.shared.metrics import get_meter
from jentic_one.shared.pagination import InvalidCursorError
from jentic_one.shared.web.errors import make_service_error_handler

_logger = structlog.get_logger(__name__)
_meter = get_meter("jentic_one.auth")
_rate_limit_counter = _meter.create_counter(
    "auth_rate_limited_requests",
    description="Count of auth-surface requests rejected by the rate limiter",
)

_ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    ActorNotFoundError: (404, "actor_not_found"),
    AgentAlreadyOwnedError: (409, "agent_already_owned"),
    ClaimActorNotAllowedError: (403, "claim_actor_not_allowed"),
    ClaimTokenInvalidError: (400, "invalid_claim_token"),
    InvalidGrantError: (400, "invalid_grant"),
    InvalidClientMetadataError: (400, "invalid_client_metadata"),
    InvalidOwnerError: (422, "invalid_owner"),
    InvalidRevocationRequestError: (400, "invalid_request"),
    InvalidTransitionError: (409, "invalid_transition"),
    NoApiKeyError: (409, "no_api_key"),
    OAuthGrantAccessDeniedError: (403, "oauth_grant_access_denied"),
    OAuthGrantNotFoundError: (404, "oauth_grant_not_found"),
    ToolkitBindingConflictError: (409, "toolkit_binding_conflict"),
    ToolkitBindingNotFoundError: (404, "toolkit_binding_not_found"),
    RateLimitExceededError: (429, "rate_limit_exceeded"),
    RegistrationAccessDeniedError: (401, "registration_access_denied"),
    OperationNotSupportedError: (403, "operation_not_supported"),
}


def _rate_limit_response_hook(
    request: Request,
    exc: Exception,
    status_code: int,
    response: JSONResponse,
) -> JSONResponse:
    """Propagate Retry-After on 429 and bump the rate-limited-requests metric."""
    if isinstance(exc, RateLimitExceededError):
        retry_after_s = max(1, math.ceil(exc.retry_after))
        response.headers["Retry-After"] = str(retry_after_s)
        _rate_limit_counter.add(1, {"path": request.url.path})
    return response


service_error_handler = make_service_error_handler(
    _ERROR_MAP, response_hook=_rate_limit_response_hook
)

# A transient DB failure that survives the in-transaction retry budget (e.g. a
# SQLite write-lock outlasting busy_timeout on the token-mint path) is infra,
# not a client fault: map it to a retryable 503 so CLIs/clients can back off,
# rather than a bare 500 that aborts `jentic setup`.
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

# A tampered/garbage pagination cursor on `GET /agents/{id}/oauth-grants` is a
# client fault, not a server one: map it to a 400 like the admin surface
# (`admin/web/errors.py`) and the registry routers do. Registered as its own
# handler because `InvalidCursorError` lives outside the `AuthServiceError`
# hierarchy (it's a shared pagination error, ultimately a ValueError). Without
# this, the standalone auth deployment 500s on `?cursor=garbage` — combined
# mode was rescued only because the admin surface registers the handler on the
# shared app.
_CURSOR_ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    InvalidCursorError: (400, "invalid_cursor"),
}

cursor_error_handler = make_service_error_handler(_CURSOR_ERROR_MAP)
