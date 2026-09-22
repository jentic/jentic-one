"""Unit tests for agent permission management endpoints (GET/PUT /agents/{id}/permissions)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.auth.services.agent_service import AgentService
from jentic_one.auth.services.errors import AuthServiceError
from jentic_one.auth.web.deps import get_agent_service
from jentic_one.auth.web.errors import service_error_handler
from jentic_one.auth.web.routers import agents
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.web.deps import resolve_identity


def _admin_identity() -> Identity:
    return Identity(
        sub="usr_admin",
        email="admin@example.com",
        permissions=["agents:write", "agents:read", "org:admin"],
    )


def _readonly_identity() -> Identity:
    return Identity(
        sub="usr_reader",
        email="reader@example.com",
        permissions=["agents:read"],
    )


@pytest.fixture()
def mock_agent_svc() -> MagicMock:
    svc = MagicMock(spec=AgentService)
    svc.get_permissions = AsyncMock(return_value=["capabilities:execute", "agents:read"])
    svc.replace_permissions = AsyncMock(return_value=["new:permission"])
    return svc


@pytest.fixture()
def client(mock_agent_svc: MagicMock) -> TestClient:
    app = FastAPI()
    app.include_router(agents.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)
    app.dependency_overrides[resolve_identity] = _admin_identity
    app.dependency_overrides[get_agent_service] = lambda: mock_agent_svc

    mock_ctx = MagicMock()
    app.state.ctx = mock_ctx
    return TestClient(app)


@pytest.fixture()
def readonly_client(mock_agent_svc: MagicMock) -> TestClient:
    app = FastAPI()
    app.include_router(agents.router)
    app.add_exception_handler(AuthServiceError, service_error_handler)
    app.dependency_overrides[resolve_identity] = _readonly_identity
    app.dependency_overrides[get_agent_service] = lambda: mock_agent_svc

    mock_ctx = MagicMock()
    app.state.ctx = mock_ctx
    return TestClient(app)


def test_get_permissions(client: TestClient) -> None:
    resp = client.get("/agents/agnt_test1/permissions")
    assert resp.status_code == 200
    assert resp.json() == {"permissions": ["capabilities:execute", "agents:read"]}


def test_get_permissions_requires_read(readonly_client: TestClient) -> None:
    resp = readonly_client.get("/agents/agnt_test1/permissions")
    assert resp.status_code == 200


def test_replace_permissions(client: TestClient) -> None:
    resp = client.put("/agents/agnt_test1/permissions", json={"permissions": ["new:permission"]})
    assert resp.status_code == 200
    assert resp.json() == {"permissions": ["new:permission"]}


def test_replace_permissions_empty(client: TestClient, mock_agent_svc: MagicMock) -> None:
    mock_agent_svc.replace_permissions = AsyncMock(return_value=[])
    resp = client.put("/agents/agnt_test1/permissions", json={"permissions": []})
    assert resp.status_code == 200
    assert resp.json() == {"permissions": []}


def test_replace_permissions_requires_write(readonly_client: TestClient) -> None:
    resp = readonly_client.put("/agents/agnt_test1/permissions", json={"permissions": ["x"]})
    assert resp.status_code == 403
