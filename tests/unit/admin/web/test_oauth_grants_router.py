"""Unit tests for the admin grant cross-view (GET /admin/oauth-grants).

The §4.8 admin listing: permission gate (`oauth-clients:read`), filter
forwarding, and the wire shape — including the consenting ``user_id`` column
(gap G10: after an agent ownership transfer the grant stays with the original
consenter, so the cross-view must show who holds it).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.admin.services.oauth_grant_admin_service import OAuthGrantAdminService
from jentic_one.admin.services.schemas.oauth_grants import OAuthGrantView
from jentic_one.admin.web.deps import get_oauth_grant_admin_service
from jentic_one.admin.web.routers.oauth_grants import router
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.pagination import Page
from jentic_one.shared.web.deps import resolve_identity


def _view() -> OAuthGrantView:
    return OAuthGrantView(
        id="ocg_1",
        oauth_client_id="oc_app",
        client_name="MCP App",
        client_origin="https://mcpapp.example.com",
        user_id="usr_consenter",
        agent_id="agt_1",
        scopes=["apis:read"],
        status="active",
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
        revoked_at=None,
        last_used_at=None,
    )


@pytest.fixture()
def mock_svc() -> MagicMock:
    svc = MagicMock(spec=OAuthGrantAdminService)
    svc.list_grants = AsyncMock(
        return_value=Page(data=[_view()], has_more=True, next_cursor="cur_next")
    )
    return svc


def _client(mock_svc: MagicMock, *, permissions: list[str]) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    identity = Identity(sub="usr_admin", email="admin@example.com", permissions=permissions)
    app.dependency_overrides[resolve_identity] = lambda: identity
    app.dependency_overrides[get_oauth_grant_admin_service] = lambda: mock_svc
    app.state.ctx = MagicMock()
    return TestClient(app)


def test_list_requires_oauth_clients_read(mock_svc: MagicMock) -> None:
    client = _client(mock_svc, permissions=["agents:read"])
    resp = client.get("/admin/oauth-grants")
    assert resp.status_code == 403
    mock_svc.list_grants.assert_not_awaited()


def test_list_returns_shape_and_pagination(mock_svc: MagicMock) -> None:
    client = _client(mock_svc, permissions=["oauth-clients:read"])
    resp = client.get("/admin/oauth-grants")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_more"] is True
    assert body["next_cursor"] == "cur_next"
    (item,) = body["data"]
    assert item["id"] == "ocg_1"
    assert item["client_name"] == "MCP App"
    assert item["client_origin"] == "https://mcpapp.example.com"
    # G10: the consenting user is part of the wire contract.
    assert item["user_id"] == "usr_consenter"
    assert item["status"] == "active"


def test_list_forwards_filters(mock_svc: MagicMock) -> None:
    client = _client(mock_svc, permissions=["oauth-clients:read"])
    resp = client.get(
        "/admin/oauth-grants",
        params={
            "client_id": "oc_app",
            "agent_id": "agt_1",
            "user_id": "usr_x",
            "status": "revoked",
            "limit": 7,
            "cursor": "cur_prev",
        },
    )
    assert resp.status_code == 200
    kwargs = mock_svc.list_grants.call_args.kwargs
    assert kwargs == {
        "client_id": "oc_app",
        "agent_id": "agt_1",
        "user_id": "usr_x",
        "status": "revoked",
        "limit": 7,
        "cursor": "cur_prev",
    }


def test_list_rejects_unknown_status(mock_svc: MagicMock) -> None:
    client = _client(mock_svc, permissions=["oauth-clients:read"])
    resp = client.get("/admin/oauth-grants", params={"status": "bogus"})
    assert resp.status_code == 422
    mock_svc.list_grants.assert_not_awaited()
