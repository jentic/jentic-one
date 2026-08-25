"""Central problem+json exception handlers for the broker (the B-004 handler).

Maps the domain-exception taxonomy (``broker/core/exceptions.py``) to
``application/problem+json`` and **also** overrides FastAPI's
``RequestValidationError`` and Starlette's ``HTTPException`` (including the
catch-all ``404``) so no default FastAPI/Starlette ``{"detail": …}`` shape ever
leaks — every response the broker emits is RFC 9457 ``problem+json``.

Every error carries the agent-recovery contract: ``error_origin`` (+ the
``Jentic-Error-Origin`` header) and, when present, the ``agent_directive``
extension member.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette import exceptions as starlette_exceptions

from jentic_one.broker.core.exceptions import (
    ActionDeniedError,
    AgentDirective,
    AmbiguousMatchError,
    BrokerError,
    CircuitOpenError,
    CredentialIdentityMismatchError,
    CredentialNeedsReconnectError,
    CredentialNotProvisionedError,
    CredentialRefreshTransientError,
    CredentialUndecryptableError,
    DeadlineExceededError,
    ErrorOrigin,
    IdempotencyConflictError,
    IdempotencyInProgressError,
    InvalidCredentialNameError,
    InvalidRevisionPinError,
    MethodNotAllowedError,
    MutationRequiresIdempotencyKeyError,
    OperationNotFoundError,
    PayloadTooLargeError,
    RateLimitExceededError,
    RunnerSchemeUnsupportedError,
    RunnerUnavailableError,
    TooManyCandidatesError,
    UnauthorizedRevisionPinError,
    UpgradeNotSupportedError,
    UpstreamResponseTooLargeError,
    UpstreamTimeoutError,
    UpstreamUrlNotAllowedError,
)
from jentic_one.broker.core.headers import JenticHeader

_PROBLEM_JSON = "application/problem+json"

# Domain exception → HTTP status (plan.md §7.3 / 02-core-proxy error map).
STATUS_BY_ERROR: dict[type[BrokerError], int] = {
    ActionDeniedError: 403,
    CredentialIdentityMismatchError: 403,
    OperationNotFoundError: 404,
    AmbiguousMatchError: 409,
    MethodNotAllowedError: 405,
    TooManyCandidatesError: 503,
    InvalidCredentialNameError: 400,
    UpstreamUrlNotAllowedError: 400,
    UpgradeNotSupportedError: 426,
    PayloadTooLargeError: 413,
    MutationRequiresIdempotencyKeyError: 428,
    IdempotencyConflictError: 409,
    IdempotencyInProgressError: 409,
    InvalidRevisionPinError: 422,
    UnauthorizedRevisionPinError: 403,
    CircuitOpenError: 503,
    RateLimitExceededError: 429,
    CredentialNotProvisionedError: 424,
    CredentialUndecryptableError: 424,
    CredentialNeedsReconnectError: 401,
    CredentialRefreshTransientError: 502,
    UpstreamTimeoutError: 504,
    DeadlineExceededError: 504,
    UpstreamResponseTooLargeError: 502,
    RunnerSchemeUnsupportedError: 501,
    RunnerUnavailableError: 503,
}


def problem_response(
    status: int,
    detail: str,
    *,
    type: str = "about:blank",
    extra: dict[str, Any] | None = None,
    origin: ErrorOrigin = ErrorOrigin.BROKER,
    directive: AgentDirective | None = None,
    headers: dict[str, str] | None = None,
    instance: str | None = None,
) -> JSONResponse:
    """Build an RFC 9457 problem+json response carrying the agent-recovery contract."""
    body: dict[str, Any] = {
        "type": type,
        "title": detail,
        "status": status,
        "error_origin": origin.value,
        **(extra or {}),
    }
    if instance is not None:
        body["instance"] = instance
    if directive is not None:
        body["agent_directive"] = directive.model_dump()
    hdrs = {**(headers or {}), JenticHeader.ERROR_ORIGIN.value: origin.value}
    return JSONResponse(body, status_code=status, media_type=_PROBLEM_JSON, headers=hdrs)


async def handle_broker_error(_request: Request, exc: BrokerError) -> JSONResponse:
    """Map any ``BrokerError`` to problem+json via the status table."""
    status = STATUS_BY_ERROR.get(type(exc), 500)
    return problem_response(
        status,
        exc.detail,
        type=exc.type,
        extra=exc.extra or None,
        origin=exc.origin,
        directive=exc.directive,
        headers=exc.headers or None,
        instance=exc.instance,
    )


async def handle_validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """Override FastAPI's default 422 list with a problem+json envelope."""
    return problem_response(
        422,
        "Request validation failed",
        type="about:blank#validation",
        extra={"errors": exc.errors()},
    )


async def handle_http_exception(
    _request: Request, exc: starlette_exceptions.HTTPException
) -> JSONResponse:
    """Override Starlette's default ``{"detail": …}`` (incl. catch-all 404)."""
    headers = getattr(exc, "headers", None)
    return problem_response(exc.status_code, str(exc.detail), headers=headers)


def install_broker_error_handlers(app: FastAPI) -> None:
    """Register the three problem+json handlers on the broker app."""
    app.add_exception_handler(BrokerError, handle_broker_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, handle_validation)  # type: ignore[arg-type]
    app.add_exception_handler(
        starlette_exceptions.HTTPException,
        handle_http_exception,  # type: ignore[arg-type]
    )
