"""Unit tests for the registry web error mapping.

Covers the defensive mapping added for jentic/jentic-one#642: an accidental
async lazy load surfaces as ``sqlalchemy.exc.MissingGreenlet``, which the DB
transaction wrapper maps to ``DatabaseConsistencyError``. That must map to a
known, logged 500 with a generic client detail rather than escaping as an
opaque unhandled traceback.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import structlog.testing
from fastapi import Request

from jentic_one.registry.services.errors import HostChangeRequiresOperatorError
from jentic_one.registry.web.app import get_exception_handlers
from jentic_one.registry.web.errors import service_error_handler
from jentic_one.shared.db.errors import DatabaseConsistencyError, DatabaseUnavailableError
from jentic_one.shared.discovery.errors import TooManyCandidatesError


def _make_request(path: str = "/apis/acme.com/widget/v1/revisions/x/promote") -> MagicMock:
    request = MagicMock(spec=Request)
    request.url.path = path
    return request


@pytest.mark.asyncio
async def test_consistency_error_maps_to_safe_500() -> None:
    """DatabaseConsistencyError maps to a 500 with a generic (non-leaking) detail."""
    request = _make_request()
    exc = DatabaseConsistencyError("greenlet_spawn has not been called; can't call await_only()")

    response = await service_error_handler(request, exc)

    assert response.status_code == 500
    assert response.media_type == "application/problem+json"
    body: dict[str, object] = json.loads(bytes(response.body))
    assert body["type"] == "internal_error"
    assert body["detail"] == "An unexpected error occurred"
    assert "greenlet" not in json.dumps(body)


@pytest.mark.asyncio
async def test_consistency_error_logged_with_raw_message() -> None:
    """The raw SQLAlchemy message is still logged server-side for diagnosis."""
    request = _make_request()
    exc = DatabaseConsistencyError("greenlet_spawn has not been called")

    with structlog.testing.capture_logs() as logs:
        await service_error_handler(request, exc)

    error_logs = [log for log in logs if log["log_level"] == "error"]
    assert len(error_logs) == 1
    assert error_logs[0]["event"] == "unhandled_service_error"
    assert error_logs[0]["type"] == "internal_error"


def test_consistency_error_handler_is_registered() -> None:
    """The registry app registers a handler for DatabaseConsistencyError."""
    registered = {exc_class for exc_class, _ in get_exception_handlers()}
    assert DatabaseConsistencyError in registered


@pytest.mark.asyncio
async def test_database_unavailable_maps_to_safe_503() -> None:
    """A transient DB failure is a retryable 503 that never echoes the driver message."""
    exc = DatabaseUnavailableError("connection is closed host=db.internal user=registry_user")
    assert DatabaseUnavailableError in {exc_class for exc_class, _ in get_exception_handlers()}

    response = await service_error_handler(_make_request(), exc)

    assert response.status_code == 503
    body: dict[str, object] = json.loads(bytes(response.body))
    assert body["type"] == "database_unavailable"
    assert body["detail"] == "The database is temporarily unavailable; please retry."
    assert "db.internal" not in json.dumps(body)


@pytest.mark.asyncio
async def test_host_change_requires_operator_maps_to_403() -> None:
    """A promote that changes a credential-bound API's server hosts is a 403."""
    exc = HostChangeRequiresOperatorError(
        "rev-1", current_hosts=["old.example.com"], new_hosts=["new.example.com"]
    )

    response = await service_error_handler(_make_request(), exc)

    assert response.status_code == 403
    body: dict[str, object] = json.loads(bytes(response.body))
    assert body["type"] == "host_change_requires_operator"
    # Pinned whole: the detail names the origins and states the rule, and carries
    # nothing about who has bound the API's credentials. Matched against the
    # template rather than by substring — the message interpolates host names, so
    # a bare ``"bound" not in detail`` would also trip on a host such as
    # ``outbound.example.com``.
    assert str(body["detail"]) == (
        "Revision 'rev-1' changes the API's server origins from [old.example.com] "
        "to [new.example.com]; making it current requires 'org:admin'"
    )


@pytest.mark.asyncio
async def test_too_many_candidates_is_a_registry_500() -> None:
    """The registry keeps its own status for the shared discovery error.

    The broker renders the same ``TooManyCandidatesError`` as 503
    ``too_many_candidates`` (pinned in ``tests/unit/broker/test_problem_json.py``);
    the registry answers its own lookup, so an overloaded index is its 500.
    """
    response = await service_error_handler(_make_request("/inspect"), TooManyCandidatesError())

    assert response.status_code == 500
    body = json.loads(bytes(response.body))
    assert body["type"] == "url_index_overloaded"
