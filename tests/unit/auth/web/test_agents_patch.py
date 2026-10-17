"""Unit tests for PATCH /agents/{agent_id}."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.auth.services.agent_service import AgentService
from jentic_one.auth.services.errors import ActorNotFoundError, AuthServiceError
from jentic_one.auth.services.schemas.agents import AgentView
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


def _agent_view(**overrides: object) -> AgentView:
    defaults: dict[str, object] = {
        "id": "agnt_test123",
        "name": "my-agent",
        "description": "A test agent",
        "owner_id": "usr_admin",
        "registered_by": "self",
        "parent_agent_id": None,
        "approved_by": None,
        "status": "active",
        "denial_reason": None,
        "denied_by": None,
        "created_at": datetime(2026, 6, 23, tzinfo=UTC),
        "approved_at": None,
    }
    defaults.update(overrides)
    return AgentView(**defaults)  # type: ignore[arg-type]


@pytest.fixture()
def mock_agent_svc() -> MagicMock:
    svc = MagicMock(spec=AgentService)
    svc.update_agent = AsyncMock(return_value=_agent_view(owner_id="usr_new"))
    svc.get_agent = AsyncMock(return_value=_agent_view())
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


def test_patch_agent_200(client: TestClient) -> None:
    resp = client.patch("/agents/agnt_test123", json={"owner_id": "usr_new"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["owner_id"] == "usr_new"


def test_patch_agent_404(client: TestClient, mock_agent_svc: MagicMock) -> None:
    mock_agent_svc.update_agent = AsyncMock(side_effect=ActorNotFoundError("agnt_missing"))

    resp = client.patch("/agents/agnt_missing", json={"name": "new-name"})

    assert resp.status_code == 404


def test_patch_agent_requires_agents_write(readonly_client: TestClient) -> None:
    resp = readonly_client.patch("/agents/agnt_test123", json={"name": "new-name"})

    assert resp.status_code == 403


def test_patch_agent_empty_body_returns_current(
    client: TestClient, mock_agent_svc: MagicMock
) -> None:
    """An empty body goes through ``update_agent`` (which owns the no-op and
    its ownership check) with no fields to change."""
    resp = client.patch("/agents/agnt_test123", json={})

    assert resp.status_code == 200
    mock_agent_svc.update_agent.assert_awaited_once()
    assert mock_agent_svc.update_agent.call_args.kwargs["update_data"] == {}
    mock_agent_svc.get_agent.assert_not_called()
