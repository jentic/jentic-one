"""Unit tests for verify_token — claim handling and permission-resolution routing.

The per-verification ``users.active`` re-check (#1139) is a real DB read; it is
stubbed here so these tests stay I/O-free, and exercised end to end against a
real database in ``tests/integration/shared/auth/test_verify_user_status.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.shared.auth.tokens import InvalidTokenError, issue_jwt
from jentic_one.shared.auth.verify import verify_token


@pytest.fixture()
def mock_ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.admin_db = MagicMock()
    return ctx


@pytest.fixture(autouse=True)
def user_is_active() -> Iterator[AsyncMock]:
    """Stub the users-row liveness read (default: the user is active)."""
    with patch(
        "jentic_one.shared.auth.verify._user_is_active", new=AsyncMock(return_value=True)
    ) as stub:
        yield stub


@pytest.mark.asyncio
async def test_user_jwt_refused_when_user_inactive(
    mock_ctx: MagicMock, user_is_active: AsyncMock
) -> None:
    """A validly-signed user JWT is refused once the user row is inactive or missing."""
    user_is_active.return_value = False
    secret = "a" * 32
    claims = {"sub": "usr_off", "actor_type": "user", "permissions": ["org:admin"]}
    token = issue_jwt(claims=claims, secret=secret, ttl_seconds=3600)

    with pytest.raises(InvalidTokenError, match="not an active user"):
        await verify_token(token, secret=secret, ctx=mock_ctx)
    user_is_active.assert_awaited_once_with(mock_ctx, "usr_off")


@pytest.mark.asyncio
@patch("jentic_one.shared.auth.verify.resolve_permissions_for_actor")
async def test_agent_jwt_skips_user_status_check(
    mock_resolve: AsyncMock, mock_ctx: MagicMock, user_is_active: AsyncMock
) -> None:
    """The users-row re-check applies to user tokens only."""
    mock_resolve.return_value = ([], [])
    secret = "a" * 32
    claims = {"sub": "agt_1", "actor_type": "agent", "parent_actor_id": "usr_owner"}
    token = issue_jwt(claims=claims, secret=secret, ttl_seconds=3600)

    identity = await verify_token(token, secret=secret, ctx=mock_ctx)

    assert identity.sub == "agt_1"
    user_is_active.assert_not_awaited()


@pytest.mark.asyncio
@patch("jentic_one.shared.auth.verify.resolve_permissions_for_actor")
async def test_jwt_with_embedded_permissions_skips_db(
    mock_resolve: AsyncMock, mock_ctx: MagicMock
) -> None:
    """JWT that already carries ``permissions`` in claims skips permission resolution."""
    secret = "a" * 32
    claims = {
        "sub": "usr_1",
        "email": "test@example.com",
        "permissions": ["org:admin", "users:read"],
        "actor_type": "user",
    }
    token = issue_jwt(claims=claims, secret=secret, ttl_seconds=3600)

    identity = await verify_token(token, secret=secret, ctx=mock_ctx)

    assert identity.sub == "usr_1"
    assert "org:admin" in identity.permissions
    assert "users:read" in identity.permissions
    mock_resolve.assert_not_called()


@pytest.mark.asyncio
@patch("jentic_one.shared.auth.verify.resolve_permissions_for_actor")
async def test_jwt_without_embedded_permissions_hits_db(
    mock_resolve: AsyncMock, mock_ctx: MagicMock
) -> None:
    """JWT without ``permissions`` claim triggers exactly one DB call for permission resolution."""
    mock_resolve.return_value = (["some:perm"], [])
    secret = "a" * 32
    claims = {
        "sub": "usr_2",
        "email": "user@example.com",
        "actor_type": "user",
    }
    token = issue_jwt(claims=claims, secret=secret, ttl_seconds=3600)

    identity = await verify_token(token, secret=secret, ctx=mock_ctx)

    assert identity.sub == "usr_2"
    assert "some:perm" in identity.permissions
    mock_resolve.assert_called_once()


@pytest.mark.asyncio
@patch("jentic_one.shared.auth.verify.resolve_permissions_for_actor")
async def test_jwt_merges_scopes_into_permissions(
    mock_resolve: AsyncMock, mock_ctx: MagicMock
) -> None:
    """Scopes from JWT claims are merged into the permissions list."""
    mock_resolve.return_value = (["resolved:perm"], [])
    secret = "a" * 32
    claims = {
        "sub": "usr_3",
        "email": "user@example.com",
        "actor_type": "user",
        "scopes": ["broker:execute", "read:all"],
    }
    token = issue_jwt(claims=claims, secret=secret, ttl_seconds=3600)

    identity = await verify_token(token, secret=secret, ctx=mock_ctx)

    assert "resolved:perm" in identity.permissions
    assert "broker:execute" in identity.permissions
    assert "read:all" in identity.permissions


@pytest.mark.asyncio
@patch("jentic_one.shared.auth.verify.resolve_permissions_for_actor")
async def test_jwt_with_permissions_and_scopes_merges_both(
    mock_resolve: AsyncMock, mock_ctx: MagicMock
) -> None:
    """When both permissions and scopes are present, they're merged without DB lookup."""
    secret = "a" * 32
    claims = {
        "sub": "usr_4",
        "email": "user@example.com",
        "actor_type": "user",
        "permissions": ["direct:perm"],
        "scopes": ["scope:a"],
    }
    token = issue_jwt(claims=claims, secret=secret, ttl_seconds=3600)

    identity = await verify_token(token, secret=secret, ctx=mock_ctx)

    assert "direct:perm" in identity.permissions
    assert "scope:a" in identity.permissions
    mock_resolve.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor_type_claim",
    [None, "definitely-not-a-real-actor"],
    ids=["missing", "unknown"],
)
async def test_jwt_with_bad_actor_type_rejected(
    mock_ctx: MagicMock, actor_type_claim: str | None
) -> None:
    """Fail closed (#862): a validly-signed JWT is refused when actor_type is
    absent or unrecognised — typed InvalidTokenError, never a bare ValueError."""
    secret = "a" * 32
    claims = {
        "sub": "usr_5",
        "email": "user@example.com",
        "permissions": ["org:admin"],
    }
    if actor_type_claim is not None:
        claims["actor_type"] = actor_type_claim
    token = issue_jwt(claims=claims, secret=secret, ttl_seconds=3600)

    with pytest.raises(InvalidTokenError):
        await verify_token(token, secret=secret, ctx=mock_ctx)
