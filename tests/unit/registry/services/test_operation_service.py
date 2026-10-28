"""Unit tests for operation service cursor/validation logic and projection."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.registry.services.errors import (
    NoCurrentRevisionError,
    RevisionNotFoundError,
)
from jentic_one.registry.services.operation_service import OperationService
from jentic_one.shared.pagination import (
    Cursor,
    InvalidCursorError,
    decode_cursor,
    encode_cursor,
)


def test_encode_decode_cursor_str_roundtrip() -> None:
    ts = datetime(2024, 3, 15, 12, 0, 0, tzinfo=UTC)
    id_str = "op_abc123def456"
    encoded = encode_cursor(ts, id_str)
    assert decode_cursor(encoded, id_kind="str") == Cursor(timestamp=ts, id=id_str)


def test_decode_cursor_str_allows_non_uuid() -> None:
    payload = {"t": "2024-01-01T00:00:00+00:00", "id": "op_not_a_uuid_at_all"}
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    decoded = decode_cursor(encoded, id_kind="str")
    assert decoded.id == "op_not_a_uuid_at_all"
    assert decoded.timestamp.tzinfo is not None


def test_decode_cursor_str_invalid_base64() -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor("not-base64!!!", id_kind="str")


def test_decode_cursor_str_invalid_json() -> None:
    encoded = base64.b64encode(b"not json").decode()
    with pytest.raises(InvalidCursorError):
        decode_cursor(encoded, id_kind="str")


def test_decode_cursor_str_missing_fields() -> None:
    encoded = base64.b64encode(json.dumps({"t": "2024-01-01T00:00:00"}).encode()).decode()
    with pytest.raises(InvalidCursorError):
        decode_cursor(encoded, id_kind="str")


def test_decode_cursor_str_naive_timestamp_gets_utc() -> None:
    payload = {"t": "2024-01-01T00:00:00", "id": "op_123"}
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    assert decode_cursor(encoded, id_kind="str").timestamp.tzinfo == UTC


@pytest.mark.asyncio
async def test_list_for_revision_invalid_uuid_raises_revision_not_found() -> None:
    ctx = MagicMock()
    svc = OperationService(ctx)
    with pytest.raises(RevisionNotFoundError):
        await svc.list_for_revision(
            vendor="v",
            name="n",
            version="1.0",
            revision_id="not-a-uuid",
        )


@pytest.mark.asyncio
async def test_list_for_live_revision_no_current_raises_error() -> None:
    ctx = MagicMock()
    mock_session = AsyncMock()
    ctx.registry_db.session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.registry_db.session.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_api = MagicMock()
    mock_api.current_revision_id = None

    with patch(
        "jentic_one.registry.services.operation_service.ApiRepository.get_by_identifier",
        new_callable=AsyncMock,
        return_value=mock_api,
    ):
        svc = OperationService(ctx)
        with pytest.raises(NoCurrentRevisionError):
            await svc.list_for_live_revision(vendor="v", name="n", version="1.0")


@pytest.mark.asyncio
async def test_has_more_and_trim() -> None:
    ctx = MagicMock()
    mock_session = AsyncMock()
    ctx.registry_db.session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.registry_db.session.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_api = MagicMock()
    mock_api.id = uuid.uuid4()
    mock_api.current_revision_id = uuid.uuid4()

    mock_revision = MagicMock()
    mock_revision.id = mock_api.current_revision_id
    mock_revision.servers = []

    ts_base = datetime(2024, 1, 1, tzinfo=UTC)
    mock_ops = []
    for i in range(3):
        op = MagicMock()
        op.id = f"op_{i}"
        op.method = "GET"
        op.path = f"/path/{i}"
        op.summary = f"Op {i}"
        op.description = None
        op.tags = None
        op.deprecated = False
        op.created_at = ts_base
        mock_ops.append(op)

    with (
        patch(
            "jentic_one.registry.services.operation_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=mock_api,
        ),
        patch(
            "jentic_one.registry.services.operation_service.ApiRevisionRepository.get_for_api",
            new_callable=AsyncMock,
            return_value=mock_revision,
        ),
        patch(
            "jentic_one.registry.services.operation_service.OperationRepository.list_page_for_revision",
            new_callable=AsyncMock,
            return_value=mock_ops,
        ),
    ):
        svc = OperationService(ctx)
        page = await svc.list_for_live_revision(vendor="v", name="n", version="1.0", limit=2)

    assert page.has_more is True
    assert len(page.data) == 2
    assert page.next_cursor is not None


@pytest.mark.asyncio
async def test_tags_defaults_to_empty_list() -> None:
    ctx = MagicMock()
    mock_session = AsyncMock()
    ctx.registry_db.session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.registry_db.session.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_api = MagicMock()
    mock_api.id = uuid.uuid4()
    mock_api.current_revision_id = uuid.uuid4()

    mock_revision = MagicMock()
    mock_revision.id = mock_api.current_revision_id
    mock_revision.servers = []

    op = MagicMock()
    op.id = "op_1"
    op.method = "GET"
    op.path = "/test"
    op.summary = "Test"
    op.description = None
    op.tags = None
    op.deprecated = False
    op.created_at = datetime(2024, 1, 1, tzinfo=UTC)

    with (
        patch(
            "jentic_one.registry.services.operation_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=mock_api,
        ),
        patch(
            "jentic_one.registry.services.operation_service.ApiRevisionRepository.get_for_api",
            new_callable=AsyncMock,
            return_value=mock_revision,
        ),
        patch(
            "jentic_one.registry.services.operation_service.OperationRepository.list_page_for_revision",
            new_callable=AsyncMock,
            return_value=[op],
        ),
    ):
        svc = OperationService(ctx)
        page = await svc.list_for_live_revision(vendor="v", name="n", version="1.0")

    assert page.data[0].tags == []


@pytest.mark.asyncio
async def test_host_derived_from_revision_servers() -> None:
    ctx = MagicMock()
    mock_session = AsyncMock()
    ctx.registry_db.session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.registry_db.session.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_api = MagicMock()
    mock_api.id = uuid.uuid4()
    mock_api.current_revision_id = uuid.uuid4()

    mock_server = MagicMock()
    mock_server.url = "https://api.example.com/v1"

    mock_revision = MagicMock()
    mock_revision.id = mock_api.current_revision_id
    mock_revision.servers = [mock_server]

    op = MagicMock()
    op.id = "op_1"
    op.method = "POST"
    op.path = "/users"
    op.summary = "Create user"
    op.description = "Creates a user"
    op.tags = ["users", "admin"]
    op.deprecated = False
    op.created_at = datetime(2024, 1, 1, tzinfo=UTC)

    with (
        patch(
            "jentic_one.registry.services.operation_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=mock_api,
        ),
        patch(
            "jentic_one.registry.services.operation_service.ApiRevisionRepository.get_for_api",
            new_callable=AsyncMock,
            return_value=mock_revision,
        ),
        patch(
            "jentic_one.registry.services.operation_service.OperationRepository.list_page_for_revision",
            new_callable=AsyncMock,
            return_value=[op],
        ),
    ):
        svc = OperationService(ctx)
        page = await svc.list_for_live_revision(vendor="v", name="n", version="1.0")

    assert page.data[0].host == "api.example.com"
    assert page.data[0].tags == ["users", "admin"]
    assert page.data[0].name == "Create user"
