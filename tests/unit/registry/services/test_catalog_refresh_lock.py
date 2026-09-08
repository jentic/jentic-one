"""Unit tests for single-flight advisory lock behavior in CatalogService._safe_refresh."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.registry.services.catalog.service import CatalogService


def _make_ctx(*, max_age: int = 300) -> MagicMock:
    ctx = MagicMock()
    ctx.config.catalog.manifest_max_age_seconds = max_age
    ctx.config.catalog.manifest_url = "https://example.com/apis.json"
    # Disable the update-notify sweep for these lock-focused tests.
    ctx.config.catalog.update_check_interval_seconds = 0
    ctx.config.ingest = MagicMock()
    mock_session = AsyncMock()
    ctx.registry_db.transaction.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.registry_db.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    ctx.registry_db.session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.registry_db.session.return_value.__aexit__ = AsyncMock(return_value=False)
    return ctx


@pytest.mark.asyncio
async def test_safe_refresh_skips_when_lock_not_acquired() -> None:
    """When advisory lock is held by another transaction, refresh is not called."""
    ctx = _make_ctx()
    svc = CatalogService(ctx)

    with (
        patch(
            "jentic_one.registry.services.catalog.service.CatalogRepository.try_acquire_refresh_lock",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch.object(svc, "refresh", new_callable=AsyncMock) as mock_refresh,
    ):
        await svc._safe_refresh()
        mock_refresh.assert_not_called()


@pytest.mark.asyncio
async def test_safe_refresh_calls_refresh_when_lock_acquired_and_stale() -> None:
    """When lock is acquired and snapshot is stale, refresh executes exactly once."""
    ctx = _make_ctx()
    svc = CatalogService(ctx)

    with (
        patch(
            "jentic_one.registry.services.catalog.service.CatalogRepository.try_acquire_refresh_lock",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "jentic_one.registry.services.catalog.service.CatalogRepository.fetched_at",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch.object(svc, "refresh", new_callable=AsyncMock) as mock_refresh,
    ):
        await svc._safe_refresh()
        mock_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_safe_refresh_skips_when_lock_acquired_but_no_longer_stale() -> None:
    """Double-check: lock acquired but another request already refreshed."""
    ctx = _make_ctx(max_age=300)
    svc = CatalogService(ctx)

    fresh_timestamp = datetime(2099, 1, 1, tzinfo=UTC)

    with (
        patch(
            "jentic_one.registry.services.catalog.service.CatalogRepository.try_acquire_refresh_lock",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "jentic_one.registry.services.catalog.service.CatalogRepository.fetched_at",
            new_callable=AsyncMock,
            return_value=fresh_timestamp,
        ),
        patch.object(svc, "refresh", new_callable=AsyncMock) as mock_refresh,
    ):
        await svc._safe_refresh()
        mock_refresh.assert_not_called()
