"""Unit tests for API update and delete service logic."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.registry.services.api_service import ApiService
from jentic_one.registry.services.errors import ApiNotFoundError
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
