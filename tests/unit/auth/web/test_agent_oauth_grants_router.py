"""Unit tests for the per-agent grant listing (GET /agents/{id}/oauth-grants).

The §4.8 "Connected clients" endpoint: response shape (including the per-item
``can_revoke`` capability, G10), query forwarding, and the web mapping of the
service-layer outcomes (owner-or-admin is enforced in
:class:`OAuthGrantService`, so here it is mocked as its error outcomes: 403
for a non-owner without admin permission, 404 for an unknown agent, 400 for a
tampered cursor) plus the unauthenticated 401 arm.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.admin.services.schemas.oauth_grants import OAuthGrantView
from jentic_one.auth.services.errors import (
    ActorNotFoundError,
    OAuthGrantAccessDeniedError,
)
from jentic_one.auth.services.oauth_grant_service import OAuthGrantService
from jentic_one.auth.web.app import get_exception_handlers
from jentic_one.auth.web.routers import oauth_grants
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.pagination import InvalidCursorError, Page
from jentic_one.shared.web.deps import resolve_identity


def _view(grant_id: str = "ocg_1", *, can_revoke: bool = True) -> OAuthGrantView:
    return OAuthGrantView(
        id=grant_id,
        oauth_client_id="oc_app",
        client_name="MCP App",
        client_origin="https://mcpapp.example.com",
        user_id="usr_consenter",
        agent_id="agt_1",
        scopes=["apis:read"],
        status="active",
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
        revoked_at=None,
        last_used_at=datetime(2026, 8, 2, tzinfo=UTC),
        can_revoke=can_revoke,
    )


def _identity() -> Identity:
    return Identity(sub="usr_owner", email="owner@example.com")


@pytest.fixture()
def mock_grant_svc() -> MagicMock:
    svc = MagicMock(spec=OAuthGrantService)
    svc.list_grants_for_agent = AsyncMock(
        return_value=Page(data=[_view()], has_more=False, next_cursor=None)
    )
    return svc


def _make_app(mock_grant_svc: MagicMock) -> FastAPI:
    app = FastAPI()
    app.include_router(oauth_grants.router)
    # The REAL registration list from the auth app factory (standalone and
    # combined-mode both consume it), so these tests prove the surface maps
    # every service outcome — notably InvalidCursorError → 400, not 500.
    for exc_class, handler in get_exception_handlers():
        app.add_exception_handler(exc_class, handler)
    app.dependency_overrides[oauth_grants.get_oauth_grant_service] = lambda: mock_grant_svc
    app.state.ctx = MagicMock()
    return app


@pytest.fixture()
def client(mock_grant_svc: MagicMock) -> TestClient:
    app = _make_app(mock_grant_svc)
    app.dependency_overrides[resolve_identity] = _identity
    return TestClient(app)


def test_list_returns_grant_shape(client: TestClient, mock_grant_svc: MagicMock) -> None:
    resp = client.get("/agents/agt_1/oauth-grants")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_more"] is False
    assert body["next_cursor"] is None
    (item,) = body["data"]
    assert item["id"] == "ocg_1"
    assert item["oauth_client_id"] == "oc_app"
    assert item["client_name"] == "MCP App"
    assert item["client_origin"] == "https://mcpapp.example.com"
    # G10: the consenting user is part of the wire contract.
    assert item["user_id"] == "usr_consenter"
    assert item["agent_id"] == "agt_1"
    assert item["scopes"] == ["apis:read"]
    assert item["status"] == "active"
    assert item["last_used_at"] is not None
    # G10: the revoke capability is part of the wire contract.
    assert item["can_revoke"] is True


def test_list_surfaces_can_revoke_false(client: TestClient, mock_grant_svc: MagicMock) -> None:
    """A viewer who may list but not revoke (post-transfer owner, read-only
    admin — the G10 divergence) sees can_revoke=false on the wire."""
    mock_grant_svc.list_grants_for_agent = AsyncMock(
        return_value=Page(data=[_view(can_revoke=False)], has_more=False, next_cursor=None)
    )
    resp = client.get("/agents/agt_1/oauth-grants")
    assert resp.status_code == 200
    assert resp.json()["data"][0]["can_revoke"] is False


def test_list_forwards_filters_and_identity(client: TestClient, mock_grant_svc: MagicMock) -> None:
    resp = client.get("/agents/agt_1/oauth-grants?status=revoked&limit=5&cursor=abc")
    assert resp.status_code == 200
    kwargs = mock_grant_svc.list_grants_for_agent.call_args.kwargs
    assert mock_grant_svc.list_grants_for_agent.call_args.args == ("agt_1",)
    assert kwargs["status"] == "revoked"
    assert kwargs["limit"] == 5
    assert kwargs["cursor"] == "abc"
    assert kwargs["identity"].sub == "usr_owner"


def test_list_rejects_unknown_status(client: TestClient) -> None:
    resp = client.get("/agents/agt_1/oauth-grants?status=bogus")
    assert resp.status_code == 422


def test_list_maps_access_denied_to_403(client: TestClient, mock_grant_svc: MagicMock) -> None:
    mock_grant_svc.list_grants_for_agent = AsyncMock(
        side_effect=OAuthGrantAccessDeniedError("agt_1")
    )
    resp = client.get("/agents/agt_1/oauth-grants")
    assert resp.status_code == 403
    assert resp.json()["type"] == "oauth_grant_access_denied"


def test_list_maps_unknown_agent_to_404(client: TestClient, mock_grant_svc: MagicMock) -> None:
    mock_grant_svc.list_grants_for_agent = AsyncMock(side_effect=ActorNotFoundError("agt_x"))
    resp = client.get("/agents/agt_x/oauth-grants")
    assert resp.status_code == 404
    assert resp.json()["type"] == "actor_not_found"


def test_list_maps_tampered_cursor_to_400(client: TestClient, mock_grant_svc: MagicMock) -> None:
    """`?cursor=garbage` is a client fault: the standalone auth surface must
    map InvalidCursorError to 400, never let it escape as a 500 (review F2)."""
    mock_grant_svc.list_grants_for_agent = AsyncMock(
        side_effect=InvalidCursorError("Invalid pagination cursor")
    )
    resp = client.get("/agents/agt_1/oauth-grants?cursor=garbage")
    assert resp.status_code == 400
    assert resp.json()["type"] == "invalid_cursor"


def test_list_unauthenticated_is_401(mock_grant_svc: MagicMock) -> None:
    """No credential → 401 through the real resolve_identity path (no
    dependency override), and the service is never consulted."""
    unauthed = TestClient(_make_app(mock_grant_svc), raise_server_exceptions=False)
    resp = unauthed.get("/agents/agt_1/oauth-grants")
    assert resp.status_code == 401
    mock_grant_svc.list_grants_for_agent.assert_not_awaited()
