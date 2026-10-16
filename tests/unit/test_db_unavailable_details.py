"""The 503 client detail for database outages on the service-error surfaces.

A connection lost while committing may already have applied the write, so its
503 must not tell the client to retry; a plain outage still may.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastapi import Request

from jentic_one.admin.web.errors import database_error_handler as admin_handler
from jentic_one.auth.web.errors import database_error_handler as auth_handler
from jentic_one.control.web.errors import database_error_handler as control_handler
from jentic_one.registry.web.errors import service_error_handler as registry_handler
from jentic_one.shared.db.errors import DatabaseCommitOutcomeUnknownError, DatabaseUnavailableError
from jentic_one.shared.web.errors import DB_COMMIT_OUTCOME_UNKNOWN_DETAIL, ServiceErrorHandler

_HANDLERS = pytest.mark.parametrize(
    "handler",
    [control_handler, admin_handler, auth_handler, registry_handler],
    ids=["control", "admin", "auth", "registry"],
)


def _request() -> MagicMock:
    request = MagicMock(spec=Request)
    request.url.path = "/things"
    return request


@_HANDLERS
@pytest.mark.asyncio
async def test_commit_outcome_unknown_does_not_invite_a_retry(
    handler: ServiceErrorHandler,
) -> None:
    response = await handler(_request(), DatabaseCommitOutcomeUnknownError("connection is closed"))

    assert response.status_code == 503
    body = json.loads(bytes(response.body))
    assert body["type"] == "database_unavailable"
    assert body["detail"] == DB_COMMIT_OUTCOME_UNKNOWN_DETAIL
    assert "please retry" not in body["detail"]


@_HANDLERS
@pytest.mark.asyncio
async def test_plain_outage_keeps_the_retry_hint(handler: ServiceErrorHandler) -> None:
    response = await handler(_request(), DatabaseUnavailableError("database is locked"))

    assert response.status_code == 503
    body = json.loads(bytes(response.body))
    assert body["detail"] == "The database is temporarily unavailable; please retry."
