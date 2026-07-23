"""The token/mint paths map a transient DB failure to a retryable 503.

Regression for issue #648: a SQLite ``database is locked`` on the token-mint
write used to bubble up as ``DatabaseUnavailableError`` with no handler, so the
auth surface returned a bare 500 and ``jentic bootstrap`` aborted. The auth app
now registers ``database_error_handler`` so it returns a retryable 503 instead.

This drives the real router + the real handlers wired in
``get_exception_handlers`` (the service is stubbed to raise the transient error
— the DB layer itself is never mocked, per project rules).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.auth.web.app import get_exception_handlers
from jentic_one.auth.web.routers import oauth
from jentic_one.shared.config import AuthConfig
from jentic_one.shared.db.errors import DatabaseUnavailableError


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(oauth.router)
    for exc_class, handler in get_exception_handlers():
        app.add_exception_handler(exc_class, handler)

    mock_ctx = MagicMock()
    mock_ctx.config.auth = AuthConfig(
        canonical_base_url="https://auth.example.com",
        assertion_max_ttl_seconds=300,
    )
    app.state.ctx = mock_ctx
    return TestClient(app, raise_server_exceptions=False)


@patch("jentic_one.auth.web.routers.oauth.AssertionService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_jwt_bearer_mint_db_unavailable_returns_503(
    mock_token_cls: MagicMock, mock_assertion_cls: MagicMock, client: TestClient
) -> None:
    """A transient admin-DB write failure on the mint path yields 503, not 500."""
    # Simulate the real wrapped error: DatabaseUnavailableError carries the raw
    # SQLAlchemy message, which includes the SQL statement and bound parameters.
    leaky_detail = (
        "(sqlite3.OperationalError) database is locked "
        "[SQL: INSERT INTO access_tokens (token_hash, actor_id) VALUES (?, ?)] "
        "[parameters: ('deadbeef', 'actor-123')]"
    )
    mock_assertion_instance = MagicMock()
    mock_assertion_instance.verify_and_exchange = AsyncMock(
        side_effect=DatabaseUnavailableError(leaky_detail)
    )
    mock_assertion_cls.return_value = mock_assertion_instance

    mock_token_instance = MagicMock()
    mock_token_instance.access_ttl_seconds = 3600
    mock_token_cls.return_value = mock_token_instance

    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": "eyJ.test.assertion",
        },
    )

    assert resp.status_code == 503
    body = resp.json()
    assert body["type"] == "database_unavailable"
    # The response body must not leak the raw SQL/params/URL (CWE-209): the
    # handler sends a static, generic detail instead of `str(exc)`.
    detail = body["detail"]
    assert "SQL:" not in detail
    assert "parameters:" not in detail
    assert "access_tokens" not in detail
    assert detail == "The database is temporarily unavailable; please retry."
