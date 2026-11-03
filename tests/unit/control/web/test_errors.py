"""Unit tests for the control web DB-error handler and the 422 enricher."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import structlog.testing
from fastapi.exceptions import RequestValidationError

from jentic_one.control.web.errors import (
    _edit_rules_validation_enricher,
    database_error_handler,
)
from jentic_one.shared.db.errors import (
    DatabaseDataError,
    DatabaseIntegrityError,
    DatabaseUnavailableError,
)

# A wrapped SQLAlchemy message as it actually surfaces on a truncation: raw SQL,
# bound parameters, and the connection URL — none of which may reach the client.
_LEAKY_DETAIL = (
    "(asyncpg.exceptions.StringDataRightTruncationError) value too long for type "
    "character varying(50) [SQL: INSERT INTO credentials (api_version) VALUES ($1)] "
    "[parameters: ('1001.0.0-SNAPSHOT-636312f2dc6e26921216979d4ae12655beeff255',)]"
)


def _make_request(path: str = "/credentials") -> MagicMock:
    request = MagicMock()
    request.url.path = path
    return request


@pytest.mark.asyncio
async def test_db_data_error_returns_400_without_leaking_sql() -> None:
    """A value-too-long DataError maps to a clean 400, not a bare 500 (#690)."""
    request = _make_request()
    exc = DatabaseDataError(_LEAKY_DETAIL)

    with structlog.testing.capture_logs() as logs:
        response = await database_error_handler(request, exc)

    assert response.status_code == 400
    body = bytes(response.body).decode()
    assert "SQL:" not in body
    assert "parameters:" not in body
    assert "character varying" not in body
    assert "A field value exceeds the maximum length allowed." in body

    warn_logs = [log for log in logs if log["log_level"] == "warning"]
    assert len(warn_logs) == 1
    assert warn_logs[0]["event"] == "client_error"
    assert warn_logs[0]["status"] == 400
    assert warn_logs[0]["raw_detail"] == _LEAKY_DETAIL


@pytest.mark.asyncio
async def test_db_integrity_error_returns_409() -> None:
    request = _make_request()
    response = await database_error_handler(request, DatabaseIntegrityError(_LEAKY_DETAIL))
    assert response.status_code == 409
    assert "SQL:" not in bytes(response.body).decode()


@pytest.mark.asyncio
async def test_db_unavailable_error_returns_503() -> None:
    request = _make_request()
    response = await database_error_handler(request, DatabaseUnavailableError(_LEAKY_DETAIL))
    assert response.status_code == 503
    assert "SQL:" not in bytes(response.body).decode()


def _rules_error(path: str, body: object) -> tuple[MagicMock, RequestValidationError]:
    request = _make_request(path)
    request.base_url = "https://one.example/"
    exc = RequestValidationError(
        [{"loc": ("body", "rules", "set"), "msg": "not a list", "type": "list_type"}]
    )
    exc.body = body
    return request, exc


@pytest.mark.asyncio
async def test_a_malformed_edit_points_at_the_access_it_names() -> None:
    """The edit route's own body names the credential: echoing it says nothing new."""
    request, exc = _rules_error(
        "/integrations:edit-connection", {"credential_id": "cred_1", "agent_id": "agnt_1"}
    )
    enriched = await _edit_rules_validation_enricher(request, exc)
    assert enriched is not None
    directive = enriched["agent_directive"]
    assert isinstance(directive, dict)
    assert directive["parameters"]["credential_id"] == "cred_1"


@pytest.mark.asyncio
async def test_a_malformed_amend_tells_the_caller_nothing_about_the_session() -> None:
    """A 422 is raised before the route checks who is asking.

    Resolving the session an amend names would hand any caller the credential
    and agent it belongs to, where the well-formed path answers the uniform
    403. So the amend path enriches nothing.
    """
    request, exc = _rules_error("/connect-sessions/cs_someone_else:amend", {"rules": "bad"})
    assert await _edit_rules_validation_enricher(request, exc) is None
