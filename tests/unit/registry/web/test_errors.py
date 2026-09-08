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

from jentic_one.registry.web.app import get_exception_handlers
from jentic_one.registry.web.errors import service_error_handler
from jentic_one.shared.db.errors import DatabaseConsistencyError


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
