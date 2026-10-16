"""Unit tests for verify_token — claim handling and permission-resolution routing.

These use agent tokens: claim handling is the same for every actor type, and a
user token adds a real ``users``-row read (#1139), which is covered against a
real database in ``tests/integration/shared/auth/test_verify_user_status.py``.
``mock_ctx`` has no usable database, so a test that reached that read fails.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.shared.auth.tokens import InvalidTokenError, issue_jwt
from jentic_one.shared.auth.verify import verify_token


@pytest.fixture()
def mock_ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.admin_db = MagicMock()
    return ctx


@pytest.mark.asyncio
@patch("jentic_one.shared.auth.verify.resolve_permissions_for_actor")
async def test_jwt_with_embedded_permissions_skips_db(
    mock_resolve: AsyncMock, mock_ctx: MagicMock
) -> None:
    """JWT that already carries ``permissions`` in claims skips permission resolution."""
    secret = "a" * 32
    claims = {
        "sub": "agt_1",
        "permissions": ["org:admin", "users:read"],
        "actor_type": "agent",
    }
    token = issue_jwt(claims=claims, secret=secret, ttl_seconds=3600)

    identity = await verify_token(token, secret=secret, ctx=mock_ctx)

    assert identity.sub == "agt_1"
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
        "sub": "agt_2",
        "actor_type": "agent",
    }
    token = issue_jwt(claims=claims, secret=secret, ttl_seconds=3600)

    identity = await verify_token(token, secret=secret, ctx=mock_ctx)

    assert identity.sub == "agt_2"
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
        "sub": "agt_3",
        "actor_type": "agent",
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
        "sub": "agt_4",
        "actor_type": "agent",
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
