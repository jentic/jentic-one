"""Unit tests for API update and delete service logic."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from jentic_one.registry.services.api_service import ApiService, _fk_violation_parts
from jentic_one.registry.services.errors import ApiDeleteBlockedError, ApiNotFoundError
from jentic_one.registry.web.errors import service_error_handler
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.db.errors import DatabaseIntegrityError

_IDENTITY = Identity(sub="usr_test", email="test@example.com")


def _make_ctx() -> MagicMock:
    ctx = MagicMock()
    mock_session = AsyncMock()
    ctx.registry_db.transaction.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.registry_db.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    ctx.registry_db.session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.registry_db.session.return_value.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_api(
    *,
    vendor: str = "acme",
    name: str = "widget",
    version: str = "1.0",
) -> MagicMock:
    api = MagicMock()
    api.id = uuid.uuid4()
    api.vendor = vendor
    api.name = name
    api.version = version
    api.display_name = "Widget API"
    api.description = "A widget"
    api.icon_url = None
    api.current_revision_id = None
    api.current_revision = None
    api.revision_count = 1
    api.operation_count = 5
    api.created_at = datetime(2024, 1, 1, tzinfo=UTC)
    api.updated_at = datetime(2024, 1, 2, tzinfo=UTC)
    return api


@pytest.mark.asyncio
async def test_update_applies_partial_fields() -> None:
    ctx = _make_ctx()
    api = _make_api()

    with (
        patch(
            "jentic_one.registry.services.api_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.api_service.ApiRepository.update_presentation",
            new_callable=AsyncMock,
            return_value=1,
        ) as mock_update,
        patch(
            "jentic_one.registry.services.api_service.ApiRepository.get_by_identifier_with_current_revision",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.api_service.CatalogUpdateCheckRepository.outdated_api_ids",
            new_callable=AsyncMock,
            return_value=set(),
        ),
    ):
        svc = ApiService(ctx)
        await svc.update(
            "acme", "widget", "1.0", fields={"display_name": "New Name"}, identity=_IDENTITY
        )

        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args
        assert call_kwargs.kwargs["fields"] == {"display_name": "New Name"}


@pytest.mark.asyncio
async def test_update_null_clears_field() -> None:
    ctx = _make_ctx()
    api = _make_api()

    with (
        patch(
            "jentic_one.registry.services.api_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.api_service.ApiRepository.update_presentation",
            new_callable=AsyncMock,
            return_value=1,
        ) as mock_update,
        patch(
            "jentic_one.registry.services.api_service.ApiRepository.get_by_identifier_with_current_revision",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.api_service.CatalogUpdateCheckRepository.outdated_api_ids",
            new_callable=AsyncMock,
            return_value=set(),
        ),
    ):
        svc = ApiService(ctx)
        await svc.update("acme", "widget", "1.0", fields={"display_name": None}, identity=_IDENTITY)

        call_kwargs = mock_update.call_args
        assert call_kwargs.kwargs["fields"] == {"display_name": None}


@pytest.mark.asyncio
async def test_update_unknown_api_raises_404() -> None:
    ctx = _make_ctx()

    with patch(
        "jentic_one.registry.services.api_service.ApiRepository.get_by_identifier",
        new_callable=AsyncMock,
        return_value=None,
    ):
        svc = ApiService(ctx)
        with pytest.raises(ApiNotFoundError):
            await svc.update(
                "acme", "missing", "1.0", fields={"display_name": "X"}, identity=_IDENTITY
            )


@pytest.mark.asyncio
async def test_delete_unknown_api_raises_404() -> None:
    ctx = _make_ctx()

    with patch(
        "jentic_one.registry.services.api_service.ApiRepository.get_by_identifier",
        new_callable=AsyncMock,
        return_value=None,
    ):
        svc = ApiService(ctx)
        with pytest.raises(ApiNotFoundError):
            await svc.delete("acme", "missing", "1.0", identity=_IDENTITY)


@pytest.mark.asyncio
async def test_delete_calls_repo_delete() -> None:
    ctx = _make_ctx()
    api = _make_api()

    with (
        patch(
            "jentic_one.registry.services.api_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.api_service.ApiRepository.delete",
            new_callable=AsyncMock,
            return_value=1,
        ) as mock_delete,
    ):
        svc = ApiService(ctx)
        await svc.delete("acme", "widget", "1.0", identity=_IDENTITY)

        mock_delete.assert_called_once_with(mock_delete.call_args.args[0], api.id)


_FK_VIOLATION = (
    "(sqlalchemy.dialects.postgresql.asyncpg.IntegrityError) <class "
    "'asyncpg.exceptions.ForeignKeyViolationError'>: update or delete on table "
    '"apis" violates foreign key constraint "fk_ext_api" on table "ext_rows"\n'
    "[SQL: DELETE FROM registry.apis WHERE registry.apis.id = $1::UUID]"
)


@pytest.mark.asyncio
async def test_delete_blocked_by_fk_raises_api_delete_blocked() -> None:
    """A non-cascading FK onto the API surfaces as ApiDeleteBlockedError, not a
    bare DatabaseIntegrityError (which the combined app maps to a generic 409)."""
    ctx = _make_ctx()
    ctx.registry_db.transaction.return_value.__aexit__ = AsyncMock(
        side_effect=DatabaseIntegrityError(_FK_VIOLATION)
    )
    api = _make_api()

    with (
        patch(
            "jentic_one.registry.services.api_service.ApiRepository.get_by_identifier",
            new_callable=AsyncMock,
            return_value=api,
        ),
        patch(
            "jentic_one.registry.services.api_service.ApiRepository.delete",
            new_callable=AsyncMock,
            return_value=1,
        ),
        patch(
            "jentic_one.registry.services.api_service.record_audit_best_effort",
            new_callable=AsyncMock,
        ) as mock_audit,
    ):
        svc = ApiService(ctx)
        with pytest.raises(ApiDeleteBlockedError) as excinfo:
            await svc.delete("acme", "widget", "1.0", identity=_IDENTITY)

    err = excinfo.value
    assert err.constraint == "fk_ext_api"
    assert err.table == "ext_rows"
    # The client-facing message names the API but never echoes SQL/internals.
    assert "acme/widget/1.0" in str(err)
    assert "SQL" not in str(err)
    assert "fk_ext_api" not in str(err)
    # Nothing was deleted, so no cross-DB cleanup and no delete audit.
    mock_audit.assert_not_called()


def test_fk_violation_parts_tolerates_unrecognised_messages() -> None:
    assert _fk_violation_parts(DatabaseIntegrityError("UNIQUE constraint failed")) == (None, None)
    assert _fk_violation_parts(DatabaseIntegrityError(_FK_VIOLATION)) == ("fk_ext_api", "ext_rows")


@pytest.mark.asyncio
async def test_api_delete_blocked_maps_to_409_with_specific_type() -> None:
    request = Request({"type": "http", "method": "DELETE", "path": "/apis/a/w/1", "headers": []})
    exc = ApiDeleteBlockedError("a", "w", "1", constraint="fk_ext_api", table="ext_rows")
    response = await service_error_handler(request, exc)

    assert response.status_code == 409
    body = json.loads(bytes(response.body))
    assert body["type"] == "api_delete_blocked"
    assert body["detail"] == str(exc)
