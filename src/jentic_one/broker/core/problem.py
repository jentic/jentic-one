"""Transport-neutral problem+json projection of the broker error taxonomy.

The single source for *which status a* ``BrokerError`` *maps to* and *what its
RFC 9457 body looks like*. The web handler (``broker/web/errors.py``) renders it
as an HTTP response; the async execution worker records the same body on a
denied job's result, so a queued execution that is refused tells the caller the
same story (``type``/``status``/``agent_directive``) the sync route would have.

Pure module — no FastAPI/Starlette imports.
"""

from __future__ import annotations

from typing import Any

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

# Domain exception → HTTP status error map.
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


def status_for_broker_error(exc: BrokerError) -> int:
    """The HTTP status a ``BrokerError`` maps to (500 for an unmapped subclass)."""
    return STATUS_BY_ERROR.get(type(exc), 500)


def problem_body(
    status: int,
    detail: str,
    *,
    type: str = "about:blank",
    extra: dict[str, Any] | None = None,
    origin: ErrorOrigin = ErrorOrigin.BROKER,
    directive: AgentDirective | None = None,
    instance: str | None = None,
) -> dict[str, Any]:
    """Build an RFC 9457 problem body carrying the agent-recovery contract."""
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
    return body


def broker_error_problem(exc: BrokerError) -> dict[str, Any]:
    """The problem body the broker's HTTP handler would render for ``exc``."""
    return problem_body(
        status_for_broker_error(exc),
        exc.detail,
        type=exc.type,
        extra=exc.extra or None,
        origin=exc.origin,
        directive=exc.directive,
        instance=exc.instance,
    )
