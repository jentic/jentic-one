"""Unit tests for the note service CRUD operations."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.registry.services.errors import (
    InvalidNoteResourceError,
    NoteNotFoundError,
    NotePreconditionFailedError,
)
from jentic_one.registry.services.note_service import NoteService
from jentic_one.shared.auth.identity import Identity

_IDENTITY = Identity(sub="usr_test", email="test@example.com")


def _make_ctx() -> MagicMock:
    ctx = MagicMock()
    mock_session = AsyncMock()
    ctx.registry_db.transaction.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.registry_db.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    ctx.registry_db.session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.registry_db.session.return_value.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_note(*, note_id: str = "note_abc123def456ghi789", revision: int = 1) -> MagicMock:
    note = MagicMock()
    note.id = note_id
    note.resource_api_id = uuid.uuid4()
    note.resource_operation_id = None
    note.resource_execution_id = None
    note.resource_credential_id = None
    note.type = "execution_feedback"
    note.body = "Test body"
    note.confidence = "observed"
    note.confidence_source = "client"
    note.source = "agent"
    note.created_by = "user@example.com"
    note.related_execution_id = None
    note.revision = revision
    note.created_at = datetime(2024, 6, 1, tzinfo=UTC)
    note.updated_at = None
    api = MagicMock()
    api.vendor = "stripe"
    api.name = "payments"
    api.version = "2023-10-16"
    note.api = api
    return note


@pytest.mark.asyncio
async def test_create_resolves_api_and_stamps_created_by() -> None:
    ctx = _make_ctx()
    api = MagicMock()
    api.id = uuid.uuid4()
    note = _make_note()

    with (
        patch(
            "jentic_one.registry.services.note_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.note_service.NoteRepository.create",
            new_callable=AsyncMock,
            return_value=note,
        ) as mock_create,
    ):
        svc = NoteService(ctx)
        view = await svc.create(
            resource_api=("stripe", "payments", "2023-10-16"),
            body="Test",
            identity=Identity(sub="user@test.com", email="test@local"),
        )

    assert view.id == note.id
    assert view.resource_api_vendor == "stripe"
    assert mock_create.call_args.kwargs["created_by"] == "user@test.com"
    assert mock_create.call_args.kwargs["resource_api_id"] == api.id


@pytest.mark.asyncio
async def test_create_rejects_zero_resources() -> None:
    ctx = _make_ctx()
    svc = NoteService(ctx)
    with pytest.raises(InvalidNoteResourceError, match="Exactly one"):
        await svc.create(body="Test", identity=_IDENTITY)


@pytest.mark.asyncio
async def test_create_rejects_multiple_resources() -> None:
    ctx = _make_ctx()
    svc = NoteService(ctx)
    with pytest.raises(InvalidNoteResourceError, match="Only one"):
        await svc.create(
            resource_api=("stripe", "payments", "2023-10-16"),
            resource_operation_id="op_123",
            body="Test",
            identity=_IDENTITY,
        )


@pytest.mark.asyncio
async def test_create_rejects_nonexistent_api() -> None:
    ctx = _make_ctx()

    with patch(
        "jentic_one.registry.services.note_service.ApiRepository.get_by_identifier",
        new_callable=AsyncMock,
        return_value=None,
    ):
        svc = NoteService(ctx)
        with pytest.raises(InvalidNoteResourceError, match="not found"):
            await svc.create(resource_api=("ghost", "api", "v1"), body="Test", identity=_IDENTITY)


@pytest.mark.asyncio
async def test_create_rejects_nonexistent_operation() -> None:
    ctx = _make_ctx()

    with patch(
        "jentic_one.registry.services.note_service.OpRepo.get_by_ids",
        new_callable=AsyncMock,
        return_value=[],
    ):
        svc = NoteService(ctx)
        with pytest.raises(InvalidNoteResourceError, match="Operation"):
            await svc.create(resource_operation_id="op_missing", body="Test", identity=_IDENTITY)


@pytest.mark.asyncio
async def test_create_links_operation_by_pk_only() -> None:
    # Notes resolve a resource operation by PK only (get_by_ids), never via the
    # inspect-time spec-operationId fallback, and persist the id verbatim.
    ctx = _make_ctx()
    note = _make_note()
    existing_op = MagicMock()

    with (
        patch(
            "jentic_one.registry.services.note_service.OpRepo.get_by_ids",
            new_callable=AsyncMock,
            return_value=[existing_op],
        ) as mock_get_by_ids,
        patch(
            "jentic_one.registry.services.note_service.NoteRepository.create",
            new_callable=AsyncMock,
            return_value=note,
        ) as mock_create,
    ):
        svc = NoteService(ctx)
        await svc.create(resource_operation_id="op_abc123", body="Test", identity=_IDENTITY)

    mock_get_by_ids.assert_awaited_once()
    await_args = mock_get_by_ids.await_args
    assert await_args is not None
    assert await_args.args[1] == {"op_abc123"}
    assert mock_create.call_args.kwargs["resource_operation_id"] == "op_abc123"


@pytest.mark.asyncio
async def test_get_returns_note_view() -> None:
    ctx = _make_ctx()
    note = _make_note()

    with patch(
        "jentic_one.registry.services.note_service.NoteRepository.get_by_id",
        new_callable=AsyncMock,
        return_value=note,
    ):
        svc = NoteService(ctx)
        view = await svc.get("note_abc123def456ghi789", identity=_IDENTITY)

    assert view.id == note.id
    assert view.body == "Test body"


@pytest.mark.asyncio
async def test_get_raises_not_found_for_missing() -> None:
    ctx = _make_ctx()

    with patch(
        "jentic_one.registry.services.note_service.NoteRepository.get_by_id",
        new_callable=AsyncMock,
        return_value=None,
    ):
        svc = NoteService(ctx)
        with pytest.raises(NoteNotFoundError):
            await svc.get("note_doesnotexist0000000", identity=_IDENTITY)


@pytest.mark.asyncio
async def test_get_raises_not_found_for_invalid_prefix() -> None:
    ctx = _make_ctx()
    svc = NoteService(ctx)
    with pytest.raises(NoteNotFoundError):
        await svc.get("bad-id-no-prefix", identity=_IDENTITY)


@pytest.mark.asyncio
async def test_update_bumps_revision() -> None:
    ctx = _make_ctx()
    note = _make_note(revision=1)
    updated_note = _make_note(revision=2)

    with (
        patch(
            "jentic_one.registry.services.note_service.NoteRepository.get_by_id",
            new_callable=AsyncMock,
            return_value=note,
        ),
        patch(
            "jentic_one.registry.services.note_service.NoteRepository.update_fields",
            new_callable=AsyncMock,
            return_value=updated_note,
        ),
    ):
        svc = NoteService(ctx)
        view = await svc.update("note_abc123def456ghi789", body="New body", identity=_IDENTITY)

    assert view.revision == 2


@pytest.mark.asyncio
async def test_update_raises_412_on_stale_if_match() -> None:
    ctx = _make_ctx()
    note = _make_note(revision=3)

    with patch(
        "jentic_one.registry.services.note_service.NoteRepository.get_by_id",
        new_callable=AsyncMock,
        return_value=note,
    ):
        svc = NoteService(ctx)
        with pytest.raises(NotePreconditionFailedError) as exc_info:
            await svc.update(
                "note_abc123def456ghi789", if_match=1, body="New body", identity=_IDENTITY
            )
        assert exc_info.value.expected == 1
        assert exc_info.value.actual == 3


@pytest.mark.asyncio
async def test_delete_calls_repo_delete() -> None:
    ctx = _make_ctx()
    note = _make_note()

    with (
        patch(
            "jentic_one.registry.services.note_service.NoteRepository.get_by_id",
            new_callable=AsyncMock,
            return_value=note,
        ),
        patch(
            "jentic_one.registry.services.note_service.NoteRepository.delete",
            new_callable=AsyncMock,
        ) as mock_delete,
    ):
        svc = NoteService(ctx)
        await svc.delete("note_abc123def456ghi789", identity=_IDENTITY)

    mock_delete.assert_called_once()


@pytest.mark.asyncio
async def test_delete_raises_412_on_stale_if_match() -> None:
    ctx = _make_ctx()
    note = _make_note(revision=5)

    with patch(
        "jentic_one.registry.services.note_service.NoteRepository.get_by_id",
        new_callable=AsyncMock,
        return_value=note,
    ):
        svc = NoteService(ctx)
        with pytest.raises(NotePreconditionFailedError):
            await svc.delete("note_abc123def456ghi789", if_match=2, identity=_IDENTITY)


@pytest.mark.asyncio
async def test_list_page_unresolvable_api_filter_returns_empty() -> None:
    ctx = _make_ctx()

    with (
        patch(
            "jentic_one.registry.services.note_service.ApiRepository.resolve_ids",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "jentic_one.registry.services.note_service.NoteRepository.list_page",
            new_callable=AsyncMock,
        ) as mock_list,
    ):
        svc = NoteService(ctx)
        page = await svc.list_page(api="ghost:api:v1", identity=_IDENTITY)

    assert page.data == []
    assert page.has_more is False
    mock_list.assert_not_called()


@pytest.mark.asyncio
async def test_list_page_resolves_api_filter_to_ids() -> None:
    ctx = _make_ctx()
    ids = [uuid.uuid4(), uuid.uuid4()]
    note = _make_note()

    with (
        patch(
            "jentic_one.registry.services.note_service.ApiRepository.resolve_ids",
            new_callable=AsyncMock,
            return_value=ids,
        ) as mock_resolve,
        patch(
            "jentic_one.registry.services.note_service.NoteRepository.list_page",
            new_callable=AsyncMock,
            return_value=[note],
        ) as mock_list,
    ):
        svc = NoteService(ctx)
        page = await svc.list_page(api="stripe", identity=_IDENTITY)

    assert len(page.data) == 1
    assert mock_resolve.call_args.kwargs == {"vendor": "stripe", "name": None, "version": None}
    assert mock_list.call_args.kwargs["api_ids"] == ids
