"""Transport-neutral problem+json projection of the broker error taxonomy.

The single source for *which status a* ``BrokerError`` (or a shared
operation-discovery error) *maps to* and *what its RFC 9457 body looks like*.
The web handler (``broker/web/errors.py``) renders it as an HTTP response; the
async execution worker records the same body on a denied job's result, so a
queued execution that is refused tells the caller the same story
(``type``/``status``/``agent_directive``) the sync route would have.

Pure module — no FastAPI/Starlette imports.
"""

from __future__ import annotations

from typing import Any

from jentic_one.broker.core.directives import AgentDirective
from jentic_one.broker.core.errors import (
    ActionDeniedError,
    AmbiguousCredentialMatchError,
    ApprovalHoldUnavailableError,
    ApprovalPendingLimitError,
    BrokerError,
    CircuitOpenError,
    CredentialBindingSuspendedError,
    CredentialIdentityMismatchError,
    CredentialNeedsReconnectError,
    CredentialNotProvisionedError,
    CredentialRefreshTransientError,
    CredentialUndecryptableError,
    DeadlineExceededError,
    IdempotencyConflictError,
    IdempotencyInProgressError,
    IdempotencyUnavailableError,
    InvalidCredentialNameError,
    InvalidRevisionPinError,
    MutationRequiresIdempotencyKeyError,
    PayloadTooLargeError,
    RateLimitExceededError,
    RunnerSchemeUnsupportedError,
    RunnerUnavailableError,
    UnauthorizedRevisionPinError,
    UpgradeNotSupportedError,
    UpstreamBodyInterruptedError,
    UpstreamResponseTooLargeError,
    UpstreamTimeoutError,
    UpstreamTransportError,
    UpstreamUrlNotAllowedError,
)
from jentic_one.shared.broker.execution import ErrorOrigin
from jentic_one.shared.discovery.errors import (
    AmbiguousMatchError,
    DiscoveryError,
    MethodNotAllowedError,
    OperationNotFoundError,
    TooManyCandidatesError,
)

# Domain exception → HTTP status error map.
STATUS_BY_ERROR: dict[type[BrokerError], int] = {
    ActionDeniedError: 403,
    ApprovalPendingLimitError: 403,
    ApprovalHoldUnavailableError: 503,
    CredentialIdentityMismatchError: 403,
    CredentialBindingSuspendedError: 403,
    AmbiguousCredentialMatchError: 409,
    InvalidCredentialNameError: 400,
    UpstreamUrlNotAllowedError: 400,
    UpgradeNotSupportedError: 426,
    PayloadTooLargeError: 413,
    MutationRequiresIdempotencyKeyError: 428,
    IdempotencyConflictError: 409,
    IdempotencyInProgressError: 409,
    IdempotencyUnavailableError: 503,
    InvalidRevisionPinError: 422,
    UnauthorizedRevisionPinError: 403,
    CircuitOpenError: 503,
    RateLimitExceededError: 429,
    CredentialNotProvisionedError: 424,
    CredentialUndecryptableError: 424,
    CredentialNeedsReconnectError: 424,
    CredentialRefreshTransientError: 502,
    UpstreamTimeoutError: 504,
    UpstreamTransportError: 502,
    DeadlineExceededError: 504,
    UpstreamResponseTooLargeError: 502,
    # Raised after the status line is sent, so normally never rendered; a
    # transport failure on the upstream leg is a gateway error if it ever is.
    UpstreamBodyInterruptedError: 502,
    RunnerSchemeUnsupportedError: 501,
    RunnerUnavailableError: 503,
}


# Shared operation-discovery error → (HTTP status, problem ``type``). Every one
# is raised before dispatch, so the upstream never saw the call; a
# ``too_many_candidates`` 503 is retryable for that reason. The registry
# renders the same error as its own 500 ``url_index_overloaded``
# (``registry/web/errors.py``): there the overloaded index is the answering
# surface's fault, here it is a dependency that failed before dispatch.
DISCOVERY_PROBLEM_BY_ERROR: dict[type[DiscoveryError], tuple[int, str]] = {
    OperationNotFoundError: (404, "operation_not_found"),
    MethodNotAllowedError: (405, "method_not_allowed"),
    AmbiguousMatchError: (409, "ambiguous_match"),
    TooManyCandidatesError: (503, "too_many_candidates"),
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
    """Build an RFC 9457 problem body carrying the agent-recovery contract.

    ``detail`` is the occurrence-specific message (required by the published
    problem-details schema). ``title`` carries the same text for backward
    compatibility with callers that read the message from ``title``.
    """
    body: dict[str, Any] = {
        "type": type,
        "title": detail,
        "status": status,
        "detail": detail,
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


def discovery_error_problem(exc: DiscoveryError, *, instance: str | None = None) -> dict[str, Any]:
    """The broker problem body for a shared operation-discovery error.

    A 405 also names the methods the URL is served for in ``allowed_methods``
    (the response carries them in ``Allow`` too). An unmapped subclass renders
    as a 500.
    """
    status, problem_type = DISCOVERY_PROBLEM_BY_ERROR.get(type(exc), (500, "about:blank"))
    extra = (
        {"allowed_methods": exc.allowed_methods} if isinstance(exc, MethodNotAllowedError) else None
    )
    return problem_body(status, exc.detail, type=problem_type, extra=extra, instance=instance)


def discovery_error_headers(exc: DiscoveryError) -> dict[str, str]:
    """Response headers a discovery-error problem carries (``Allow`` on a 405)."""
    if isinstance(exc, MethodNotAllowedError):
        return {"Allow": ", ".join(exc.allowed_methods)}
    return {}
