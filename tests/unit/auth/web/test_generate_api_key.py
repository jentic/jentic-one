"""Unit tests for :generate-api-key endpoints (agents and service accounts)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.auth.services.agent_auth_service import AgentAuthService
from jentic_one.auth.services.errors import (
    ActorNotFoundError,
    AuthServiceError,
    InvalidTransitionError,
)
from jentic_one.auth.web.deps import get_agent_auth_service
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.routers import agents
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.web.deps import resolve_identity


def _admin_identity() -> Identity:
    return Identity(
        sub="usr_admin",
        email="admin@example.com",
        permissions=["agents:write", "org:admin"],
    )


@pytest.fixture()
def mock_agent_auth_svc() -> MagicMock:
    return MagicMock(spec=AgentAuthService)


@pytest.fixture()
def client(mock_agent_auth_svc: MagicMock) -> TestClient:
    app = FastAPI()
    app.include_router(agents.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)
    app.dependency_overrides[resolve_identity] = _admin_identity
    app.dependency_overrides[get_agent_auth_service] = lambda: mock_agent_auth_svc

    mock_ctx = MagicMock()
    app.state.ctx = mock_ctx
    return TestClient(app)


# ---------------------------------------------------------------------------
# Agent API key generation
# ---------------------------------------------------------------------------


def test_generate_agent_api_key_success(mock_agent_auth_svc: MagicMock, client: TestClient) -> None:
    mock_agent_auth_svc.register_api_key = AsyncMock(return_value="jak_test_plaintext_key")

    resp = client.post("/agents/agnt_active1:generate-api-key")

    assert resp.status_code == 200
    data = resp.json()
    assert data["key"] == "jak_test_plaintext_key"


def test_generate_agent_api_key_not_found(
    mock_agent_auth_svc: MagicMock, client: TestClient
) -> None:
    mock_agent_auth_svc.register_api_key = AsyncMock(side_effect=ActorNotFoundError("agnt_missing"))

    resp = client.post("/agents/agnt_missing:generate-api-key")

    assert resp.status_code == 404


def test_generate_agent_api_key_not_active(
    mock_agent_auth_svc: MagicMock, client: TestClient
) -> None:
    mock_agent_auth_svc.register_api_key = AsyncMock(
        side_effect=InvalidTransitionError("agnt_pending", "pending", "generate-api-key")
    )

    resp = client.post("/agents/agnt_pending:generate-api-key")

    assert resp.status_code == 409
