"""Unit tests for note route handlers."""

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
    InvalidNoteResourceError,
    NoteNotFoundError,
    NotePreconditionFailedError,
)
from jentic_one.registry.services.note_service import NotePage, NoteView
from jentic_one.registry.services.schemas import NoteCreate, NoteQuery, NoteUpdate
from jentic_one.registry.web.app import get_exception_handlers
from jentic_one.registry.web.routers import notes
from jentic_one.registry.web.schemas.notes import NoteUpdateRequest
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.schemas.api_reference import APIReference
from jentic_one.shared.web.deps import resolve_identity

_NOTE_ID = "note_abc123def456ghi789jkl"


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]
    for exc_class, handler in get_exception_handlers():
        app.add_exception_handler(exc_class, handler)
    app.include_router(notes.router)

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


def _make_view(*, revision: int = 1) -> NoteView:
    return NoteView(
        id=_NOTE_ID,
        resource_api_id=uuid.uuid4(),
        resource_api_vendor="stripe",
        resource_api_name="payments",
        resource_api_version="2023-10-16",
        resource_operation_id=None,
        resource_execution_id=None,
        resource_credential_id=None,
        type="execution_feedback",
        body="This API has good error handling.",
        confidence="observed",
        confidence_source="client",
        source="agent",
        created_by="user@example.com",
        related_execution_id=None,
        revision=revision,
        created_at=datetime(2024, 6, 1, tzinfo=UTC),
        updated_at=None,
    )


_VALID_API_REF = {"vendor": "stripe", "name": "payments", "version": "2023-10-16"}


def test_create_note_201(client: TestClient) -> None:
    view = _make_view()
    with patch(
        "jentic_one.registry.web.routers.notes.NoteService.create",
        new_callable=AsyncMock,
        return_value=view,
    ) as mock_create:
        resp = client.post(
            "/notes",
            json={
                "resource": {"api": _VALID_API_REF},
                "type": "execution_feedback",
                "body": "Test note",
                "confidence": "observed",
                "source": "agent",
            },
        )

    assert resp.status_code == 201
    mock_create.assert_awaited_once()
    create_args = mock_create.await_args
    assert create_args is not None
    payload = create_args.args[0]
    assert isinstance(payload, NoteCreate)
    assert payload.resource.api == APIReference(
        vendor="stripe", name="payments", version="2023-10-16"
    )
    assert payload.body == "Test note"
    assert payload.type == "execution_feedback"
    assert type(payload.type) is str
    assert payload.confidence == "observed"
    assert type(payload.confidence) is str
    assert payload.confidence_source == "client"
    assert payload.source == "agent"
    assert type(payload.source) is str
    assert create_args.kwargs["identity"].sub == "test_user"

    body = resp.json()
    assert body["note_id"] == _NOTE_ID
    assert body["revision"] == 1
    assert body["resource"]["api"] == _VALID_API_REF
    assert body["updated_at"] is not None
    assert body["updated_at"] == body["created_at"]
    assert "_links" in body
    assert "self" in body["_links"]
    assert "/notes/" in body["_links"]["self"]
    assert body["_links"]["resource"] == ("http://testserver/apis/stripe/payments/2023-10-16")


def test_create_note_422_zero_resources(client: TestClient) -> None:
    resp = client.post(
        "/notes",
        json={
            "resource": {},
            "body": "Test note",
        },
    )
    assert resp.status_code == 422


def test_create_note_422_multiple_resources(client: TestClient) -> None:
    resp = client.post(
        "/notes",
        json={
            "resource": {"api": _VALID_API_REF, "operation_id": "op_123"},
            "body": "Test note",
        },
    )
    assert resp.status_code == 422


def test_create_note_422_invalid_resource(client: TestClient) -> None:
    with patch(
        "jentic_one.registry.web.routers.notes.NoteService.create",
        new_callable=AsyncMock,
        side_effect=InvalidNoteResourceError("API 'bad/api/v1' not found"),
    ):
        resp = client.post(
            "/notes",
            json={
                "resource": {"api": _VALID_API_REF},
                "body": "Test note",
            },
        )

    assert resp.status_code == 422


def test_create_note_422_invalid_enum(client: TestClient) -> None:
    resp = client.post(
        "/notes",
        json={
            "resource": {"api": _VALID_API_REF},
            "body": "Test note",
            "type": "not_a_real_type",
        },
    )
    assert resp.status_code == 422


def test_list_notes_200(client: TestClient) -> None:
    page = NotePage(
        data=[_make_view()],
        has_more=False,
        next_cursor=None,
    )
    with patch(
        "jentic_one.registry.web.routers.notes.NoteService.list_page",
        new_callable=AsyncMock,
        return_value=page,
    ):
        resp = client.get("/notes")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1
    assert body["has_more"] is False
    assert "_links" in body["data"][0]


def test_list_notes_with_filters(client: TestClient) -> None:
    page = NotePage(data=[], has_more=False, next_cursor=None)
    with patch(
        "jentic_one.registry.web.routers.notes.NoteService.list_page",
        new_callable=AsyncMock,
        return_value=page,
    ) as mock_list:
        resp = client.get("/notes?type=observation&created_by=user@example.com")

    assert resp.status_code == 200
    mock_list.assert_awaited_once()
    list_args = mock_list.await_args
    assert list_args is not None
    query = list_args.args[0]
    assert isinstance(query, NoteQuery)
    assert query.type == "observation"
    assert query.created_by == "user@example.com"
    assert list_args.kwargs["identity"].sub == "test_user"


def test_get_note_200_with_etag(client: TestClient) -> None:
    view = _make_view(revision=3)
    with patch(
        "jentic_one.registry.web.routers.notes.NoteService.get",
        new_callable=AsyncMock,
        return_value=view,
    ):
        resp = client.get(f"/notes/{_NOTE_ID}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["note_id"] == _NOTE_ID
    assert resp.headers["ETag"] == '"3"'


def test_get_note_404(client: TestClient) -> None:
    with patch(
        "jentic_one.registry.web.routers.notes.NoteService.get",
        new_callable=AsyncMock,
        side_effect=NoteNotFoundError(_NOTE_ID),
    ):
        resp = client.get(f"/notes/{_NOTE_ID}")

    assert resp.status_code == 404


def test_update_note_200_with_if_match(client: TestClient) -> None:
    view = _make_view(revision=2)
    with patch(
        "jentic_one.registry.web.routers.notes.NoteService.update",
        new_callable=AsyncMock,
        return_value=view,
    ) as mock_update:
        resp = client.patch(
            f"/notes/{_NOTE_ID}",
            json={"body": "Updated body", "type": "usage_hint"},
            headers={"If-Match": '"1"'},
        )

    assert resp.status_code == 200
    assert resp.headers["ETag"] == '"2"'
    assert resp.json()["revision"] == 2
    mock_update.assert_awaited_once()
    update_args = mock_update.await_args
    assert update_args is not None
    assert update_args.args[0] == _NOTE_ID
    payload = update_args.args[1]
    assert isinstance(payload, NoteUpdate)
    assert payload.model_dump(exclude_unset=True) == {
        "body": "Updated body",
        "type": "usage_hint",
    }
    assert type(payload.type) is str
    assert update_args.kwargs["if_match"] == 1
    assert update_args.kwargs["identity"].sub == "test_user"


def test_update_note_412_stale_if_match(client: TestClient) -> None:
    with patch(
        "jentic_one.registry.web.routers.notes.NoteService.update",
        new_callable=AsyncMock,
        side_effect=NotePreconditionFailedError(_NOTE_ID, 1, 3),
    ):
        resp = client.patch(
            f"/notes/{_NOTE_ID}",
            json={"body": "Updated body"},
            headers={"If-Match": '"1"'},
        )

    assert resp.status_code == 412


def test_update_note_200_without_if_match(client: TestClient) -> None:
    view = _make_view(revision=2)
    with patch(
        "jentic_one.registry.web.routers.notes.NoteService.update",
        new_callable=AsyncMock,
        return_value=view,
    ) as mock_update:
        resp = client.patch(
            f"/notes/{_NOTE_ID}",
            json={"body": "Updated body"},
        )

    assert resp.status_code == 200
    mock_update.assert_awaited_once()
    update_args = mock_update.await_args
    assert update_args is not None
    assert update_args.args[0] == _NOTE_ID
    payload = update_args.args[1]
    assert isinstance(payload, NoteUpdate)
    assert payload.model_dump(exclude_unset=True) == {"body": "Updated body"}
    assert update_args.kwargs["if_match"] is None
    assert update_args.kwargs["identity"].sub == "test_user"


def test_note_update_request_to_service_model_omitted_vs_null() -> None:
    omitted = NoteUpdateRequest.model_validate({"body": "Updated body"}).to_service_model()
    assert omitted.model_dump(exclude_unset=True) == {"body": "Updated body"}
    assert "type" not in omitted.model_dump(exclude_unset=True)

    cleared = NoteUpdateRequest.model_validate({"type": None}).to_service_model()
    assert cleared.model_dump(exclude_unset=True) == {"type": None}


def test_update_note_400_malformed_if_match(client: TestClient) -> None:
    with patch(
        "jentic_one.registry.web.routers.notes.NoteService.update",
        new_callable=AsyncMock,
        return_value=_make_view(revision=2),
    ):
        resp = client.patch(
            f"/notes/{_NOTE_ID}",
            json={"body": "Updated body"},
            headers={"If-Match": "not-a-number"},
        )

    assert resp.status_code == 400


def test_update_note_400_null_body(client: TestClient) -> None:
    resp = client.patch(
        f"/notes/{_NOTE_ID}",
        json={"body": None},
    )
    assert resp.status_code == 422


def test_delete_note_204(client: TestClient) -> None:
    with patch(
        "jentic_one.registry.web.routers.notes.NoteService.delete",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = client.delete(f"/notes/{_NOTE_ID}")

    assert resp.status_code == 204


def test_delete_note_412_stale_if_match(client: TestClient) -> None:
    with patch(
        "jentic_one.registry.web.routers.notes.NoteService.delete",
        new_callable=AsyncMock,
        side_effect=NotePreconditionFailedError(_NOTE_ID, 1, 3),
    ):
        resp = client.delete(
            f"/notes/{_NOTE_ID}",
            headers={"If-Match": '"1"'},
        )

    assert resp.status_code == 412
