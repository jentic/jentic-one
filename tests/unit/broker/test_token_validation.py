"""Unit tests for broker token validation cache."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from jentic_one.broker.core.token_validation import CachedTokenValidator
from jentic_one.shared.auth.errors import TokenValidationError
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorType
from jentic_one.shared.scopes import BROKER_EXECUTE_SCOPE


def _make_identity(
    *,
    sub: str = "agnt_test1",
    actor_type: ActorType = ActorType.AGENT,
    permissions: list[str] | None = None,
    expires_at: datetime | None = None,
    active: bool = True,
) -> Identity:
    return Identity(
        sub=sub,
        actor_type=actor_type,
        permissions=permissions or [BROKER_EXECUTE_SCOPE],
        expires_at=expires_at or (datetime.now(UTC) + timedelta(hours=1)),
        active=active,
    )


@pytest.fixture()
def mock_resolver() -> AsyncMock:
    resolver = AsyncMock()
    resolver.resolve_access_token = AsyncMock(return_value=_make_identity())
    return resolver


@pytest.fixture()
def validator(mock_resolver: AsyncMock) -> CachedTokenValidator:
    return CachedTokenValidator(resolver=mock_resolver, cache_ttl_seconds=5.0)


@pytest.mark.asyncio
async def test_validate_returns_identity(
    validator: CachedTokenValidator, mock_resolver: AsyncMock
) -> None:
    result = await validator.validate("at_valid_token")
    assert result.sub == "agnt_test1"
    assert result.actor_type == ActorType.AGENT
    mock_resolver.resolve_access_token.assert_called_once_with("at_valid_token")


@pytest.mark.asyncio
async def test_validate_raises_on_unknown_token(mock_resolver: AsyncMock) -> None:
    mock_resolver.resolve_access_token.return_value = None
    validator = CachedTokenValidator(resolver=mock_resolver)

    with pytest.raises(TokenValidationError, match="unknown_token"):
        await validator.validate("at_unknown")


@pytest.mark.asyncio
async def test_validate_raises_on_inactive_token(mock_resolver: AsyncMock) -> None:
    mock_resolver.resolve_access_token.return_value = _make_identity(active=False)
    validator = CachedTokenValidator(resolver=mock_resolver)

    with pytest.raises(TokenValidationError, match="token_inactive"):
        await validator.validate("at_inactive")


@pytest.mark.asyncio
async def test_validate_raises_on_expired_token(mock_resolver: AsyncMock) -> None:
    mock_resolver.resolve_access_token.return_value = _make_identity(
        expires_at=datetime.now(UTC) - timedelta(minutes=1)
    )
    validator = CachedTokenValidator(resolver=mock_resolver)

    with pytest.raises(TokenValidationError, match="token_expired"):
        await validator.validate("at_expired")


@pytest.mark.asyncio
async def test_cache_hit_avoids_second_resolver_call(
    validator: CachedTokenValidator, mock_resolver: AsyncMock
) -> None:
    await validator.validate("at_token_x")
    await validator.validate("at_token_x")
    mock_resolver.resolve_access_token.assert_called_once()


@pytest.mark.asyncio
async def test_cache_miss_after_ttl_expired(mock_resolver: AsyncMock) -> None:
    validator = CachedTokenValidator(resolver=mock_resolver, cache_ttl_seconds=0.01)

    await validator.validate("at_token_y")
    time.sleep(0.02)
    await validator.validate("at_token_y")

    assert mock_resolver.resolve_access_token.call_count == 2


@pytest.mark.asyncio
async def test_negative_cache_entry(mock_resolver: AsyncMock) -> None:
    mock_resolver.resolve_access_token.return_value = None
    validator = CachedTokenValidator(resolver=mock_resolver, cache_ttl_seconds=5.0)

    with pytest.raises(TokenValidationError, match="unknown_token"):
        await validator.validate("at_bad")
    with pytest.raises(TokenValidationError, match="unknown_token"):
        await validator.validate("at_bad")

    mock_resolver.resolve_access_token.assert_called_once()


@pytest.mark.asyncio
async def test_expires_at_override_despite_active_flag(mock_resolver: AsyncMock) -> None:
    """Token marked active=True but past expires_at is still rejected."""
    mock_resolver.resolve_access_token.return_value = _make_identity(
        active=True, expires_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    validator = CachedTokenValidator(resolver=mock_resolver)

    with pytest.raises(TokenValidationError, match="token_expired"):
        await validator.validate("at_edge_case")


@pytest.mark.asyncio
async def test_invalidate_forces_cache_miss(
    validator: CachedTokenValidator, mock_resolver: AsyncMock
) -> None:
    await validator.validate("at_invalidate_me")
    validator.invalidate("at_invalidate_me")
    await validator.validate("at_invalidate_me")

    assert mock_resolver.resolve_access_token.call_count == 2


@pytest.mark.asyncio
async def test_clear_removes_all_entries(
    validator: CachedTokenValidator, mock_resolver: AsyncMock
) -> None:
    await validator.validate("at_a")
    await validator.validate("at_b")
    validator.clear()
    await validator.validate("at_a")
    await validator.validate("at_b")

    assert mock_resolver.resolve_access_token.call_count == 4
