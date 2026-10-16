"""Unit tests for verify_token — claim handling and permission-resolution routing.

User tokens re-check the ``users`` row on every verification (#1139), so the
``user_ctx`` fixture backs ``ctx.admin_db`` with a real SQLite database holding
the row the token names. Revocation and outage paths are covered in
``tests/integration/shared/auth/test_verify_user_status.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

from jentic_one.shared.auth.tokens import InvalidTokenError, issue_jwt
from jentic_one.shared.auth.verify import verify_token
from jentic_one.shared.config import DatabaseConfig
from jentic_one.shared.db.session import DatabaseSession

_USER_IDS = ("usr_1", "usr_2", "usr_3", "usr_4")


@pytest.fixture()
def mock_ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.admin_db = MagicMock()
    return ctx


@pytest.fixture()
async def user_ctx(tmp_path: Path) -> AsyncIterator[MagicMock]:
    """A context whose admin DB is real SQLite with an active row per test user."""
    db = DatabaseSession(DatabaseConfig(backend="sqlite", path=str(tmp_path / "admin.db")))
    await db.connect()
    async with db.transaction() as session:
        await session.execute(
            text(
                "CREATE TABLE users (id TEXT PRIMARY KEY, active BOOLEAN NOT NULL,"
                " session_generation INTEGER NOT NULL DEFAULT 0)"
            )
        )
        for user_id in _USER_IDS:
            await session.execute(
                text("INSERT INTO users (id, active) VALUES (:id, 1)"), {"id": user_id}
            )
    ctx = MagicMock()
    ctx.admin_db = db
    try:
        yield ctx
    finally:
        await db.close()


@pytest.mark.asyncio
@patch("jentic_one.shared.auth.verify.resolve_permissions_for_actor")
async def test_jwt_with_embedded_permissions_skips_db(
    mock_resolve: AsyncMock, user_ctx: MagicMock
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

    identity = await verify_token(token, secret=secret, ctx=user_ctx)

    assert identity.sub == "usr_1"
    assert identity.email == "test@example.com"
    assert "org:admin" in identity.permissions
    assert "users:read" in identity.permissions
    mock_resolve.assert_not_called()


@pytest.mark.asyncio
@patch("jentic_one.shared.auth.verify.resolve_permissions_for_actor")
async def test_jwt_without_embedded_permissions_hits_db(
    mock_resolve: AsyncMock, user_ctx: MagicMock
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

    identity = await verify_token(token, secret=secret, ctx=user_ctx)

    assert identity.sub == "usr_2"
    assert identity.email == "user@example.com"
    assert "some:perm" in identity.permissions
    mock_resolve.assert_called_once()


@pytest.mark.asyncio
@patch("jentic_one.shared.auth.verify.resolve_permissions_for_actor")
async def test_jwt_merges_scopes_into_permissions(
    mock_resolve: AsyncMock, user_ctx: MagicMock
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

    identity = await verify_token(token, secret=secret, ctx=user_ctx)

    assert "resolved:perm" in identity.permissions
    assert "broker:execute" in identity.permissions
    assert "read:all" in identity.permissions


@pytest.mark.asyncio
@patch("jentic_one.shared.auth.verify.resolve_permissions_for_actor")
async def test_jwt_with_permissions_and_scopes_merges_both(
    mock_resolve: AsyncMock, user_ctx: MagicMock
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

    identity = await verify_token(token, secret=secret, ctx=user_ctx)

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
