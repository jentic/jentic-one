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

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette import exceptions as starlette_exceptions

from jentic_one.broker.core.exceptions import (
    AgentDirective,
    BrokerError,
    ErrorOrigin,
)
from jentic_one.broker.core.headers import JenticHeader
from jentic_one.broker.core.problem import problem_body, status_for_broker_error
from jentic_one.shared.db.errors import DatabaseCommitOutcomeUnknownError, DatabaseUnavailableError
from jentic_one.shared.web.errors import (
    DB_COMMIT_OUTCOME_UNKNOWN_DETAIL,
    log_database_unavailable,
    sanitize_validation_errors,
)

_PROBLEM_JSON = "application/problem+json"

logger = structlog.get_logger(__name__)


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
    body = problem_body(
        status,
        detail,
        type=type,
        extra=extra,
        origin=origin,
        directive=directive,
        instance=instance,
    )
    hdrs = {**(headers or {}), JenticHeader.ERROR_ORIGIN.value: origin.value}
    return JSONResponse(body, status_code=status, media_type=_PROBLEM_JSON, headers=hdrs)


def broker_error_response(exc: BrokerError) -> JSONResponse:
    """The problem+json response a ``BrokerError`` renders to (status from the table)."""
    status = status_for_broker_error(exc)
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


async def handle_broker_error(_request: Request, exc: BrokerError) -> JSONResponse:
    """Map any ``BrokerError`` to problem+json via the status table."""
    return broker_error_response(exc)


async def handle_validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """Override FastAPI's default 422 list with a problem+json envelope.

    The submitted ``input`` (and value-echoing ``ctx``) is stripped from each
    error item so a rejected request never reflects its values back.
    """
    return problem_response(
        422,
        "Request validation failed",
        type="about:blank#validation",
        extra={"errors": sanitize_validation_errors(exc.errors())},
    )


async def handle_http_exception(
    _request: Request, exc: starlette_exceptions.HTTPException
) -> JSONResponse:
    """Override Starlette's default ``{"detail": …}`` (incl. catch-all 404)."""
    headers = getattr(exc, "headers", None)
    return problem_response(exc.status_code, str(exc.detail), headers=headers)


async def handle_database_unavailable(
    request: Request, exc: DatabaseUnavailableError
) -> JSONResponse:
    """Answer a transient broker-DB failure (e.g. a lost pooled connection) with a 503.

    The client gets a static detail — the wrapped driver message can carry
    connection internals (CWE-209). The server log carries the error type and
    the short, SQL-free detail at warning level, without a traceback: the
    chained SQLAlchemy cause renders the statement and its bound parameters.
    No retry directive is attached: the failure can land after the upstream
    call was dispatched (e.g. persisting its record), so blindly re-sending a
    non-idempotent call could execute it twice. A connection lost at commit
    gets the shared outcome-unknown detail, which asks the client to check
    whether the write was applied before retrying.
    """
    log_database_unavailable(logger, "broker_database_unavailable", exc, path=request.url.path)
    detail = (
        DB_COMMIT_OUTCOME_UNKNOWN_DETAIL
        if isinstance(exc, DatabaseCommitOutcomeUnknownError)
        else "The broker's database is temporarily unavailable"
    )
    return problem_response(
        503,
        detail,
        type="database_unavailable",
        instance=request.url.path,
    )


def install_broker_error_handlers(app: FastAPI) -> None:
    """Register the problem+json handlers on the broker app."""
    app.add_exception_handler(BrokerError, handle_broker_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, handle_validation)  # type: ignore[arg-type]
    app.add_exception_handler(
        starlette_exceptions.HTTPException,
        handle_http_exception,  # type: ignore[arg-type]
    )
    app.add_exception_handler(
        DatabaseUnavailableError,
        handle_database_unavailable,  # type: ignore[arg-type]
    )
