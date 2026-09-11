"""Wire-shape tests for the token endpoint's RFC 6749 §5.2 error dialect (#1252).

``POST /oauth/token`` is a spec-facing endpoint parsed by real OAuth/MCP
clients (mcp-remote, Cursor), which understand only the §5.2 body —
``{"error": …, "error_description": …}``. The platform's Problem Details
handlers must never answer here: a revoked-grant client that receives
``{"type": "invalid_grant", …}`` cannot classify the failure and wedges,
replaying its dead refresh token forever instead of restarting authorization.
These tests pin the exact wire shape per error arm so the dialect can't
regress to Problem Details (the ``_TokenRoute`` reshaping in
``auth/web/routers/oauth.py``).
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.auth.services.errors import AuthServiceError, InvalidGrantError
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.routers import oauth
from jentic_one.shared.config import AuthConfig


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(oauth.router)
    # The Problem Details handler is REGISTERED (as in the real app) — the
    # dialect tests prove the route-level reshaping wins before it ever runs.
    app.add_exception_handler(AuthServiceError, service_error_handler)

    mock_ctx = MagicMock()
    mock_ctx.config.auth = AuthConfig(canonical_base_url="https://auth.example.com")
    app.state.ctx = mock_ctx
    return TestClient(app)


def _assert_rfc6749_shape(body: dict[str, object]) -> None:
    """The §5.2 members and NOTHING of the Problem Details envelope."""
    assert set(body) == {"error", "error_description"}


@patch("jentic_one.auth.web.routers.oauth.OAuthClientService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_refresh_with_revoked_grant_speaks_rfc6749(
    mock_token_cls: MagicMock, mock_oauth_svc_cls: MagicMock, client: TestClient
) -> None:
    """THE #1252 arm: a G10-revoked consent grant answers §5.2 invalid_grant.

    ``TokenService.refresh`` re-checks the consent grant on every rotation and
    raises ``InvalidGrantError("consent grant has been revoked")``
    (token_service.py); the wire shape must let an RFC-compliant client see
    ``error=invalid_grant`` and restart authorization.
    """
    mock_token_svc = MagicMock(access_ttl_seconds=3600)
    mock_token_svc.refresh = AsyncMock(
        side_effect=InvalidGrantError("consent grant has been revoked")
    )
    mock_token_cls.return_value = mock_token_svc
    mock_oauth_svc_cls.return_value = MagicMock()

    resp = client.post(
        "/oauth/token",
        json={"grant_type": "refresh_token", "refresh_token": "rt_revoked_grant"},
    )

    assert resp.status_code == 400
    body = resp.json()
    _assert_rfc6749_shape(body)
    assert body == {
        "error": "invalid_grant",
        "error_description": "consent grant has been revoked",
    }
    # §5.2 error responses carry the same no-store posture as §5.1 successes.
    assert resp.headers["Cache-Control"] == "no-store"
    assert resp.headers["Pragma"] == "no-cache"


@patch("jentic_one.auth.web.routers.oauth.OAuthClientService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_refresh_with_failed_client_authentication_is_invalid_client(
    mock_token_cls: MagicMock, mock_oauth_svc_cls: MagicMock, client: TestClient
) -> None:
    """§5.2 names failed client authentication ``invalid_client``."""
    mock_oauth_svc = MagicMock()
    mock_oauth_svc.verify_client_secret = AsyncMock(return_value=False)
    mock_oauth_svc_cls.return_value = mock_oauth_svc
    mock_token_svc = MagicMock(access_ttl_seconds=3600)
    mock_token_svc.refresh = AsyncMock()
    mock_token_cls.return_value = mock_token_svc

    resp = client.post(
        "/oauth/token",
        json={
            "grant_type": "refresh_token",
            "refresh_token": "rt_any",
            "client_id": "oc_conf_client",
            "client_secret": "jcs_wrong",
        },
    )

    assert resp.status_code == 400
    body = resp.json()
    _assert_rfc6749_shape(body)
    assert body["error"] == "invalid_client"
    assert body["error_description"] == "client authentication failed"
    mock_token_svc.refresh.assert_not_awaited()


@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_unknown_grant_type_is_unsupported_grant_type(
    mock_token_cls: MagicMock, client: TestClient
) -> None:
    """§5.2 names an unknown grant type ``unsupported_grant_type``."""
    mock_token_cls.return_value = MagicMock(access_ttl_seconds=3600)

    resp = client.post("/oauth/token", json={"grant_type": "password"})

    assert resp.status_code == 400
    body = resp.json()
    _assert_rfc6749_shape(body)
    assert body["error"] == "unsupported_grant_type"
    assert body["error_description"] == "unsupported grant_type: password"


@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_dependency_raised_parse_error_speaks_the_dialect_too(
    mock_token_cls: MagicMock, client: TestClient
) -> None:
    """Errors raised in the DEPENDENCY chain (``_parse_token_request``) are
    reshaped as well — the route-class wrapper sits outside dependency
    solving, so no arm of the endpoint can leak Problem Details. A malformed
    request is §5.2 ``invalid_request`` (fix and retry), never
    ``invalid_grant`` (which would tell an RFC-compliant client to restart
    authorization over a request-shape bug)."""
    mock_token_cls.return_value = MagicMock(access_ttl_seconds=3600)

    resp = client.post("/oauth/token", content=b"", headers={"content-type": "application/json"})

    assert resp.status_code == 400
    body = resp.json()
    _assert_rfc6749_shape(body)
    assert body == {"error": "invalid_request", "error_description": "request body is required"}


@patch("jentic_one.auth.web.routers.oauth.OAuthClientService")
@patch("jentic_one.auth.web.routers.oauth.TokenService")
def test_basic_auth_failure_is_401_with_www_authenticate(
    mock_token_cls: MagicMock, mock_oauth_svc_cls: MagicMock, client: TestClient
) -> None:
    """§5.2 MUST: failed client authentication that was ATTEMPTED via the
    ``Authorization`` header (HTTP Basic, RFC 6749 §2.3.1) answers 401 with a
    matching ``WWW-Authenticate`` challenge — same §5.2 dialect body as the
    body-credential 400 arm."""
    mock_oauth_svc = MagicMock()
    mock_oauth_svc.verify_client_secret = AsyncMock(return_value=False)
    mock_oauth_svc_cls.return_value = mock_oauth_svc
    mock_token_svc = MagicMock(access_ttl_seconds=3600)
    mock_token_svc.refresh = AsyncMock()
    mock_token_cls.return_value = mock_token_svc

    credentials = base64.b64encode(b"oc_conf_client:jcs_wrong").decode()
    resp = client.post(
        "/oauth/token",
        json={"grant_type": "refresh_token", "refresh_token": "rt_any"},
        headers={"Authorization": f"Basic {credentials}"},
    )

    assert resp.status_code == 401
    body = resp.json()
    _assert_rfc6749_shape(body)
    assert body["error"] == "invalid_client"
    assert resp.headers["WWW-Authenticate"].startswith("Basic")
    mock_token_svc.refresh.assert_not_awaited()


def test_rate_limited_token_request_speaks_slow_down(client: TestClient) -> None:
    """The 429 arm speaks the dialect (``error=slow_down`` per RFC 8628 §3.5)
    with ``Retry-After`` intact — mirroring the RFC 7009 form arm."""
    outcome = MagicMock(allowed=False, retry_after_s=2.0)
    limiter = MagicMock()
    limiter.acquire = AsyncMock(return_value=outcome)
    client.app.state._token_limiter = limiter  # type: ignore[attr-defined]

    resp = client.post(
        "/oauth/token",
        json={"grant_type": "refresh_token", "refresh_token": "rt_any"},
    )

    assert resp.status_code == 429
    body = resp.json()
    _assert_rfc6749_shape(body)
    assert body["error"] == "slow_down"
    assert resp.headers["Retry-After"] == "2"
