"""Unit tests verifying error handler emits logs at correct levels."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import structlog.testing

from jentic_one.admin.services.errors import (
    AccountLockedError,
    AdminServiceError,
    InvalidCredentialsError,
    InvalidInputError,
    UserNotFoundError,
)
from jentic_one.admin.web.errors import database_error_handler, service_error_handler
from jentic_one.shared.db.errors import DatabaseIntegrityError, DatabaseUnavailableError
from jentic_one.shared.pagination import InvalidCursorError

# A wrapped SQLAlchemy message as it actually surfaces: raw SQL, bound
# parameters, and the connection URL — none of which may reach the client.
_LEAKY_DETAIL = (
    "(sqlite3.OperationalError) database is locked "
    "[SQL: INSERT INTO users (email, password_hash) VALUES (?, ?)] "
    "[parameters: ('a@b.com', 'hunter2')]"
)


def _make_request(path: str = "/test") -> MagicMock:
    request = MagicMock()
    request.url.path = path
    return request


@pytest.mark.asyncio
async def test_4xx_error_logs_warning() -> None:
    request = _make_request("/users/abc")
    exc = UserNotFoundError("abc")

    with structlog.testing.capture_logs() as logs:
        response = await service_error_handler(request, exc)

    assert response.status_code == 404

    warn_logs = [log for log in logs if log["log_level"] == "warning"]
    assert len(warn_logs) == 1
    assert warn_logs[0]["event"] == "client_error"
    assert warn_logs[0]["status"] == 404
    assert warn_logs[0]["type"] == "user_not_found"
    assert warn_logs[0]["path"] == "/users/abc"


@pytest.mark.asyncio
async def test_400_error_logs_warning() -> None:
    request = _make_request("/invites/redeem")
    exc = InvalidInputError("Password too short")

    with structlog.testing.capture_logs() as logs:
        response = await service_error_handler(request, exc)

    assert response.status_code == 400

    warn_logs = [log for log in logs if log["log_level"] == "warning"]
    assert len(warn_logs) == 1
    assert warn_logs[0]["event"] == "client_error"
    assert warn_logs[0]["status"] == 400


class _UnmappedServerError(AdminServiceError):
    """An error subclass that is not in the error map."""


@pytest.mark.asyncio
async def test_account_locked_response_matches_invalid_credentials() -> None:
    """A locked account is answered byte-for-byte like a wrong password (no
    account-existence oracle); the lockout is only visible in the server log."""
    with structlog.testing.capture_logs() as logs:
        locked = await service_error_handler(
            _make_request("/auth/login"), AccountLockedError("usr_locked")
        )
    wrong = await service_error_handler(_make_request("/auth/login"), InvalidCredentialsError())

    assert locked.status_code == wrong.status_code == 401
    assert locked.media_type == wrong.media_type
    assert bytes(locked.body) == bytes(wrong.body)
    assert b"usr_locked" not in bytes(locked.body)

    warn_logs = [log for log in logs if log["log_level"] == "warning"]
    assert len(warn_logs) == 1
    assert warn_logs[0]["type"] == "invalid_credentials"
    assert warn_logs[0]["raw_detail"] == "Account 'usr_locked' is temporarily locked"


@pytest.mark.asyncio
async def test_invalid_cursor_returns_400() -> None:
    request = _make_request("/actors")
    exc = InvalidCursorError("Invalid pagination cursor")

    with structlog.testing.capture_logs() as logs:
        response = await service_error_handler(request, exc)

    assert response.status_code == 400

    warn_logs = [log for log in logs if log["log_level"] == "warning"]
    assert len(warn_logs) == 1
    assert warn_logs[0]["event"] == "client_error"
    assert warn_logs[0]["type"] == "invalid_cursor"


@pytest.mark.asyncio
async def test_unmapped_error_logs_error_at_500() -> None:

    request = _make_request("/something")
    exc = _UnmappedServerError("boom")

    with structlog.testing.capture_logs() as logs:
        response = await service_error_handler(request, exc)

    assert response.status_code == 500

    error_logs = [log for log in logs if log["log_level"] == "error"]
    assert len(error_logs) == 1
    assert error_logs[0]["event"] == "unknown_service_error"
    assert error_logs[0]["path"] == "/something"


@pytest.mark.asyncio
async def test_db_unavailable_returns_503_without_leaking_sql() -> None:
    request = _make_request("/users")
    exc = DatabaseUnavailableError(_LEAKY_DETAIL)

    with structlog.testing.capture_logs() as logs:
        response = await database_error_handler(request, exc)

    assert response.status_code == 503
    body = bytes(response.body).decode()
    assert "SQL:" not in body
    assert "parameters:" not in body
    assert "password_hash" not in body
    assert "The database is temporarily unavailable" in body

    # A lost database is infrastructure, not a code fault: one warning with
    # type names and the SQL-free detail, no traceback (the chained SQLAlchemy
    # cause renders the statement and its bound parameters).
    [entry] = logs
    assert entry["log_level"] == "warning"
    assert entry["event"] == "database_unavailable"
    assert "exc_info" not in entry
    assert entry["error_type"] == "DatabaseUnavailableError"
    rendered = repr(entry)
    assert "SQL:" not in rendered
    assert "password_hash" not in rendered


@pytest.mark.asyncio
async def test_db_integrity_returns_409_without_leaking_sql() -> None:
    request = _make_request("/users")
    exc = DatabaseIntegrityError(_LEAKY_DETAIL)

    with structlog.testing.capture_logs() as logs:
        response = await database_error_handler(request, exc)

    assert response.status_code == 409
    body = bytes(response.body).decode()
    assert "SQL:" not in body
    assert "parameters:" not in body

    warn_logs = [log for log in logs if log["log_level"] == "warning"]
    assert len(warn_logs) == 1
    assert warn_logs[0]["raw_detail"] == _LEAKY_DETAIL
