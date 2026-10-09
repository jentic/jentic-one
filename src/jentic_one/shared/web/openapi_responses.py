"""Reusable OpenAPI error-response catalogues for control-plane routers.

Every control-plane operation answers errors with RFC 9457 Problem Details
(``application/problem+json``) using the shared
:class:`jentic.problem_details.ProblemDetail` model. Rather than repeat the
error shape on every route, routers attach one of the catalogues below via
``include_router(..., responses=...)``, so the generated spec documents the
standard failure modes consistently.

The catalogue entries use ``model=ProblemDetail`` only; the media type is
normalised to ``application/problem+json`` and a per-status example is injected
during OpenAPI generation (see
:func:`jentic_one.shared.web.openapi_meta.install_openapi_metadata`).
"""

from __future__ import annotations

from typing import Any

from jentic.problem_details import ProblemDetail

PROBLEM_JSON = "application/problem+json"

# Per-status canonical Problem Details, keyed by HTTP status code (as the string
# OpenAPI uses for response keys). Consumed by the OpenAPI post-processor to add
# an ``example`` to each error response.
STATUS_EXAMPLES: dict[str, dict[str, Any]] = {
    "400": {
        "type": "bad_request",
        "title": "Bad Request",
        "status": 400,
        "detail": "The request was malformed or failed a precondition.",
        "instance": "/example/path",
    },
    "401": {
        "type": "unauthorized",
        "title": "Unauthorized",
        "status": 401,
        "detail": "Authentication is required or the bearer token is invalid.",
        "instance": "/example/path",
    },
    "403": {
        "type": "forbidden",
        "title": "Forbidden",
        "status": 403,
        "detail": "The authenticated principal lacks the required permission.",
        "instance": "/example/path",
    },
    "404": {
        "type": "not_found",
        "title": "Not Found",
        "status": 404,
        "detail": "The requested resource does not exist or is not visible to you.",
        "instance": "/example/path",
    },
    "409": {
        "type": "conflict",
        "title": "Conflict",
        "status": 409,
        "detail": "The request conflicts with the current state of the resource.",
        "instance": "/example/path",
    },
    "410": {
        "type": "gone",
        "title": "Gone",
        "status": 410,
        "detail": "The resource is no longer available and the operation has self-closed.",
        "instance": "/example/path",
    },
    "422": {
        "type": "validation_error",
        "title": "Unprocessable Entity",
        "status": 422,
        "detail": "Request validation failed; see errors[] for details.",
        "instance": "/example/path",
        "errors": [{"detail": "Field 'name' must not be blank.", "pointer": "#/name"}],
    },
    "429": {
        "type": "rate_limit_exceeded",
        "title": "Too Many Requests",
        "status": 429,
        "detail": "Too many requests; retry after the indicated delay.",
        "instance": "/example/path",
    },
    "500": {
        "type": "server_error",
        "title": "Internal Server Error",
        "status": 500,
        "detail": "An unexpected error occurred.",
        "instance": "/example/path",
    },
    "503": {
        # Control-plane 503s come from the shared DB-error handler, which emits
        # `database_unavailable` (a transient DB failure that outlasted the retry
        # budget). Keep this example aligned with the runtime `type` and `detail`.
        "type": "database_unavailable",
        "title": "Service Unavailable",
        "status": 503,
        "detail": "The database is temporarily unavailable; please retry.",
        "instance": "/example/path",
    },
}


def _entry(description: str) -> dict[str, Any]:
    return {"description": description, "model": ProblemDetail}


# Attached to every authenticated operation.
COMMON_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: _entry("Bad Request"),
    401: _entry("Unauthorized"),
    403: _entry("Forbidden"),
    422: _entry("Unprocessable Entity"),
    500: _entry("Internal Server Error"),
    503: _entry("Service Unavailable"),
}

# Unauthenticated endpoints (health, login, callbacks): no 401/403.
PUBLIC_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: _entry("Bad Request"),
    422: _entry("Unprocessable Entity"),
    500: _entry("Internal Server Error"),
    503: _entry("Service Unavailable"),
}


def with_responses(*extra: dict[int | str, dict[str, Any]]) -> dict[int | str, dict[str, Any]]:
    """Merge :data:`COMMON_ERROR_RESPONSES` with route-specific additions."""
    merged: dict[int | str, dict[str, Any]] = dict(COMMON_ERROR_RESPONSES)
    for block in extra:
        merged.update(block)
    return merged


def not_found(description: str = "Not Found") -> dict[int | str, dict[str, Any]]:
    """A 404 entry for routes that can miss."""
    return {404: _entry(description)}


def conflict(description: str = "Conflict") -> dict[int | str, dict[str, Any]]:
    """A 409 entry for routes with state conflicts."""
    return {409: _entry(description)}


def gone(description: str = "Gone") -> dict[int | str, dict[str, Any]]:
    """A 410 entry for one-time / self-closing routes (e.g. first-run setup)."""
    return {410: _entry(description)}


def too_many_requests(description: str = "Too Many Requests") -> dict[int | str, dict[str, Any]]:
    """A 429 entry for routes with a rate limit or a quota."""
    return {429: _entry(description)}
