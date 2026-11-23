"""Unit tests for ``POST /auth/login`` rate-limit wiring and failure semantics (#1441)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.admin.web import ratelimit as admin_ratelimit
from jentic_one.admin.web.deps import get_auth_service
from jentic_one.admin.web.routers import auth as admin_auth_router
from jentic_one.auth.web import ratelimit as auth_ratelimit
from jentic_one.shared.config import AuthConfig
from jentic_one.shared.state.backend import MemoryStateBackend, RateLimitDecision
from jentic_one.shared.web import ratelimit as shared_ratelimit
from jentic_one.shared.web.errors import unhandled_exception_handler

_TEST_PW = "test-password-123"  # pragma: allowlist secret


def test_shared_and_auth_ratelimit_expose_identical_client_network_helpers() -> None:
    """Client-network normalization lives once in ``shared.web.ratelimit``."""
    assert auth_ratelimit.IPV6_CLIENT_PREFIX == shared_ratelimit.IPV6_CLIENT_PREFIX == 64
    assert auth_ratelimit.client_ip is shared_ratelimit.client_ip
    assert auth_ratelimit.client_network is shared_ratelimit.client_network
    assert auth_ratelimit.client_rate_limit_key is shared_ratelimit.client_rate_limit_key


def test_missing_admin_state_backend_is_a_wiring_error() -> None:
    """An app mounting ``POST /auth/login`` without ``admin_state_backend`` fails loudly."""
    ctx = MagicMock()
    ctx.config.auth = AuthConfig()
    auth_svc = MagicMock()
    auth_svc.login = AsyncMock()

    app = FastAPI()
    app.include_router(admin_auth_router.router)
    app.state.ctx = ctx
    app.dependency_overrides[get_auth_service] = lambda: auth_svc

    client = TestClient(app, raise_server_exceptions=True)
    with pytest.raises(RuntimeError, match="admin_state_backend"):
        client.post(
            "/auth/login",
            json={"email": "admin@example.com", "password": _TEST_PW},
        )
    auth_svc.login.assert_not_awaited()
    assert getattr(app.state, admin_ratelimit.ADMIN_STATE_BACKEND_ATTR, None) is None


def test_transient_state_backend_error_fails_closed_without_running_login() -> None:
    """A state-backend outage fails closed (500 problem+json) rather than bypassing the limit."""

    class _OutageBackend(MemoryStateBackend):
        async def token_bucket(
            self, key: str, *, rate: float, burst: int, cost: int = 1
        ) -> RateLimitDecision:
            raise ConnectionError("redis unreachable")

    ctx = MagicMock()
    ctx.config.auth = AuthConfig()
    auth_svc = MagicMock()
    auth_svc.login = AsyncMock()

    app = FastAPI()
    app.include_router(admin_auth_router.router)
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.state.ctx = ctx
    app.state.admin_state_backend = _OutageBackend()
    app.dependency_overrides[get_auth_service] = lambda: auth_svc

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": _TEST_PW},
    )
    assert resp.status_code == 500
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["type"] == "server_error"
    auth_svc.login.assert_not_awaited()
