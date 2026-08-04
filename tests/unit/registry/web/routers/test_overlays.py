"""Unit tests for overlay route handlers."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jentic.problem_details import ProblemDetailException, problem_detail_exception_handler

from jentic_one.registry.services.errors import (
    ApiNotFoundError,
    OverlayNotFoundError,
    OverlayRematerializeForbiddenError,
    OverlayStateConflictError,
)
from jentic_one.registry.services.overlay_service import OverlayPage, OverlayPageItem, OverlayView
from jentic_one.registry.web.app import get_exception_handlers
from jentic_one.registry.web.routers import overlays
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.web.deps import resolve_identity

_OVERLAY_ID = "ovr_abc123def456ghi789jkl"


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]
    for exc_class, handler in get_exception_handlers():
        app.add_exception_handler(exc_class, handler)
    app.include_router(overlays.router)

    mock_session = AsyncMock()
    mock_db = MagicMock()

    @asynccontextmanager
    async def _fake_session() -> AsyncGenerator[AsyncMock, None]:
        yield mock_session

    mock_db.session = _fake_session
    mock_db.transaction = _fake_session
    mock_ctx = MagicMock()
    mock_ctx.registry_db = mock_db

    app.state.ctx = mock_ctx

    _test_identity = Identity(sub="test_user", email="test@test.com", permissions=["org:admin"])
    app.dependency_overrides[resolve_identity] = lambda: _test_identity

    return TestClient(app, headers={"Authorization": "Bearer test-token"})


def _make_view(
    *, status: str = "pending", confirmed_revision_id: uuid.UUID | None = None
) -> OverlayView:
    return OverlayView(
        id=_OVERLAY_ID,
        api_id=uuid.uuid4(),
        vendor="acme",
        name="pets",
        version="v1",
        status=status,
        document={"overlay": "1.0"},
        target_revision_id=None,
        confirmed_revision_id=confirmed_revision_id,
        contributed_by="agent",
        confirmed_by_execution_id=None,
        created_at=datetime(2024, 6, 1, tzinfo=UTC),
        updated_at=None,
        confirmed_at=None,
        deprecated_at=None,
    )


def test_submit_overlay_201(client: TestClient) -> None:
    view = _make_view()
    with patch(
        "jentic_one.registry.web.routers.overlays.OverlayService.submit",
        new_callable=AsyncMock,
        return_value=view,
    ):
        resp = client.post(
            "/apis/acme/pets/v1/overlays",
            json={"document": {"overlay": "1.0"}},
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == _OVERLAY_ID
    assert body["status"] == "pending"
    assert "_links" in body
    assert "self" in body["_links"]
    assert "/overlays/" in body["_links"]["self"]


def test_submit_overlay_api_not_found(client: TestClient) -> None:
    with patch(
        "jentic_one.registry.web.routers.overlays.OverlayService.submit",
        new_callable=AsyncMock,
        side_effect=ApiNotFoundError("acme", "missing", "v1"),
    ):
        resp = client.post(
            "/apis/acme/missing/v1/overlays",
            json={"document": {"x": 1}},
        )

    assert resp.status_code == 404


def test_get_overlay_surfaces_confirmed_revision_id(client: TestClient) -> None:
    """A materialized overlay exposes its confirmed_revision_id over the API."""
    rev_id = uuid.uuid4()
    view = _make_view(status="confirmed", confirmed_revision_id=rev_id)
    with patch(
        "jentic_one.registry.web.routers.overlays.OverlayService.get",
        new_callable=AsyncMock,
        return_value=view,
    ):
        resp = client.get(f"/apis/acme/pets/v1/overlays/{_OVERLAY_ID}")

    assert resp.status_code == 200
    assert resp.json()["confirmed_revision_id"] == str(rev_id)


def test_list_overlays_200(client: TestClient) -> None:
    page = OverlayPage(
        data=[
            OverlayPageItem(
                id=_OVERLAY_ID,
                api_id=uuid.uuid4(),
                status="pending",
                document={"x": 1},
                target_revision_id=None,
                confirmed_revision_id=None,
                contributed_by=None,
                confirmed_by_execution_id=None,
                created_at=datetime(2024, 6, 1, tzinfo=UTC),
                updated_at=None,
                confirmed_at=None,
                deprecated_at=None,
            )
        ],
        has_more=False,
        next_cursor=None,
    )
    with patch(
        "jentic_one.registry.web.routers.overlays.OverlayService.list_page",
        new_callable=AsyncMock,
        return_value=page,
    ):
        resp = client.get("/apis/acme/pets/v1/overlays")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1
    assert body["has_more"] is False
    assert "_links" in body["data"][0]


def test_get_overlay_200(client: TestClient) -> None:
    view = _make_view()
    with patch(
        "jentic_one.registry.web.routers.overlays.OverlayService.get",
        new_callable=AsyncMock,
        return_value=view,
    ):
        resp = client.get(f"/apis/acme/pets/v1/overlays/{_OVERLAY_ID}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == _OVERLAY_ID
    assert "_links" in body


def test_get_overlay_not_found(client: TestClient) -> None:
    with patch(
        "jentic_one.registry.web.routers.overlays.OverlayService.get",
        new_callable=AsyncMock,
        side_effect=OverlayNotFoundError(_OVERLAY_ID, "acme", "pets", "v1"),
    ):
        resp = client.get(f"/apis/acme/pets/v1/overlays/{_OVERLAY_ID}")

    assert resp.status_code == 404


def test_update_overlay_200(client: TestClient) -> None:
    view = _make_view()
    with patch(
        "jentic_one.registry.web.routers.overlays.OverlayService.update",
        new_callable=AsyncMock,
        return_value=view,
    ):
        resp = client.patch(
            f"/apis/acme/pets/v1/overlays/{_OVERLAY_ID}",
            json={"document": {"updated": True}},
        )

    assert resp.status_code == 200
    assert "_links" in resp.json()


def test_update_overlay_conflict(client: TestClient) -> None:
    with patch(
        "jentic_one.registry.web.routers.overlays.OverlayService.update",
        new_callable=AsyncMock,
        side_effect=OverlayStateConflictError(_OVERLAY_ID, "confirmed", ["pending"], "update"),
    ):
        resp = client.patch(
            f"/apis/acme/pets/v1/overlays/{_OVERLAY_ID}",
            json={"document": {"x": 1}},
        )

    assert resp.status_code == 409


def test_update_overlay_rematerialize_forbidden(client: TestClient) -> None:
    """Editing a materialized overlay without overlays:confirm maps to 403 (D1)."""
    with patch(
        "jentic_one.registry.web.routers.overlays.OverlayService.update",
        new_callable=AsyncMock,
        side_effect=OverlayRematerializeForbiddenError(_OVERLAY_ID),
    ):
        resp = client.patch(
            f"/apis/acme/pets/v1/overlays/{_OVERLAY_ID}",
            json={"document": {"x": 1}},
        )

    assert resp.status_code == 403


def test_deprecate_overlay_204(client: TestClient) -> None:
    with patch(
        "jentic_one.registry.web.routers.overlays.OverlayService.deprecate",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = client.delete(f"/apis/acme/pets/v1/overlays/{_OVERLAY_ID}")

    assert resp.status_code == 204


def test_confirm_overlay_200(client: TestClient) -> None:
    view = _make_view(status="confirmed")
    with patch(
        "jentic_one.registry.web.routers.overlays.OverlayService.confirm",
        new_callable=AsyncMock,
        return_value=view,
    ):
        resp = client.post(
            f"/apis/acme/pets/v1/overlays/{_OVERLAY_ID}:confirm",
            json={},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "confirmed"
    assert "_links" in body


def test_confirm_overlay_conflict(client: TestClient) -> None:
    with patch(
        "jentic_one.registry.web.routers.overlays.OverlayService.confirm",
        new_callable=AsyncMock,
        side_effect=OverlayStateConflictError(_OVERLAY_ID, "deprecated", ["pending"], "confirm"),
    ):
        resp = client.post(
            f"/apis/acme/pets/v1/overlays/{_OVERLAY_ID}:confirm",
            json={},
        )

    assert resp.status_code == 409


def test_confirm_link_present_when_pending(client: TestClient) -> None:
    view = _make_view(status="pending")
    with patch(
        "jentic_one.registry.web.routers.overlays.OverlayService.get",
        new_callable=AsyncMock,
        return_value=view,
    ):
        resp = client.get(f"/apis/acme/pets/v1/overlays/{_OVERLAY_ID}")

    body = resp.json()
    assert body["_links"]["confirm"] is not None
    assert ":confirm" in body["_links"]["confirm"]


def test_confirm_link_absent_when_confirmed(client: TestClient) -> None:
    view = _make_view(status="confirmed")
    with patch(
        "jentic_one.registry.web.routers.overlays.OverlayService.get",
        new_callable=AsyncMock,
        return_value=view,
    ):
        resp = client.get(f"/apis/acme/pets/v1/overlays/{_OVERLAY_ID}")

    body = resp.json()
    assert body["_links"]["confirm"] is None
