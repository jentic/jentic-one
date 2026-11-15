"""Unit tests for verify_token — claim handling and permission-resolution routing.

User tokens re-check the ``users`` row on every verification (#1139), so the
``user_ctx`` fixture backs ``ctx.admin_db`` with a real SQLite database holding
the row the token names. Revocation and outage paths are covered in
``tests/integration/shared/auth/test_verify_user_status.py``.
"""

from __future__ import annotations

import hashlib
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
    """A context whose admin DB is real SQLite with active user and agent rows."""
    db = DatabaseSession(DatabaseConfig(backend="sqlite", path=str(tmp_path / "admin.db")))
    await db.connect()
    async with db.transaction() as session:
        await session.execute(
            text(
                "CREATE TABLE users (id TEXT PRIMARY KEY, active BOOLEAN NOT NULL,"
                " session_generation INTEGER NOT NULL DEFAULT 0, deleted_at DATETIME)"
            )
        )
        await session.execute(
            text("CREATE TABLE agents (id TEXT PRIMARY KEY, status TEXT NOT NULL, owner_id TEXT)")
        )
        for user_id in _USER_IDS:
            await session.execute(
                text("INSERT INTO users (id, active) VALUES (:id, 1)"), {"id": user_id}
            )
        await session.execute(
            text(
                "INSERT INTO agents (id, status, owner_id) VALUES"
                " ('agnt_1', 'active', 'usr_1'),"
                " ('agnt_unowned', 'active', NULL)"
            )
        )
    ctx = MagicMock()
    ctx.admin_db = db
    ctx.is_db_allowed.return_value = True
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
    mock_resolve.return_value = ["some:perm"]
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
    mock_resolve.return_value = ["resolved:perm"]
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


@pytest.mark.asyncio
async def test_jwt_for_deleted_user_rejected_even_when_row_reads_active(
    user_ctx: MagicMock,
) -> None:
    """Delete is terminal: a row carrying ``deleted_at`` refuses its JWT even if
    ``active`` reads true, with the current generation on the token."""
    async with user_ctx.admin_db.transaction() as session:
        await session.execute(
            text("UPDATE users SET deleted_at = CURRENT_TIMESTAMP WHERE id = 'usr_1'")
        )
    secret = "a" * 32
    token = issue_jwt(
        claims={"sub": "usr_1", "actor_type": "user", "permissions": []},
        secret=secret,
        ttl_seconds=3600,
    )

    with pytest.raises(InvalidTokenError):
        await verify_token(token, secret=secret, ctx=user_ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_sub",
    [None, "", "   ", 123, True, ["usr_1"]],
    ids=["missing_none", "empty", "whitespace", "int", "bool", "list"],
)
async def test_verify_token_rejects_invalid_sub_claim(user_ctx: MagicMock, bad_sub: object) -> None:
    """Missing, empty, whitespace, or non-string ``sub`` raises ``InvalidTokenError``."""
    claims: dict[str, object] = {"actor_type": "user", "permissions": []}
    if bad_sub is not None:
        claims["sub"] = bad_sub
    with (
        patch("jentic_one.shared.auth.verify.decode_jwt", return_value=claims),
        pytest.raises(InvalidTokenError, match="valid subject"),
    ):
        await verify_token("stub.jwt.token", secret="a" * 32, ctx=user_ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_parent",
    ["", "   ", 123, True, ["usr_1"]],
    ids=["empty", "whitespace", "int", "bool", "list"],
)
async def test_verify_token_rejects_invalid_parent_actor_id(
    user_ctx: MagicMock, bad_parent: object
) -> None:
    """Present but empty/whitespace/non-string ``parent_actor_id`` raises ``InvalidTokenError``."""
    claims: dict[str, object] = {
        "sub": "agnt_1",
        "actor_type": "agent",
        "parent_actor_id": bad_parent,
        "permissions": [],
    }
    with (
        patch("jentic_one.shared.auth.verify.decode_jwt", return_value=claims),
        pytest.raises(InvalidTokenError, match="invalid parent_actor_id"),
    ):
        await verify_token("stub.jwt.token", secret="a" * 32, ctx=user_ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bad_sgen", "db_generation"),
    [
        (False, 0),
        (True, 1),
        ("0", 0),
        (-1, 0),
        (1.5, 1),
    ],
    ids=["bool_false_vs_0", "bool_true_vs_1", "str_zero", "negative", "float"],
)
async def test_verify_token_rejects_non_int_or_boolean_sgen(
    user_ctx: MagicMock, bad_sgen: object, db_generation: int
) -> None:
    """Boolean, string, float, or negative ``sgen`` is rejected even if equal to DB generation."""
    async with user_ctx.admin_db.transaction() as session:
        await session.execute(
            text("UPDATE users SET session_generation = :gen WHERE id = 'usr_1'"),
            {"gen": db_generation},
        )
    secret = "a" * 32  # pragma: allowlist secret
    token = issue_jwt(
        claims={"sub": "usr_1", "actor_type": "user", "permissions": [], "sgen": bad_sgen},
        secret=secret,
        ttl_seconds=3600,
    )

    with pytest.raises(InvalidTokenError, match="invalid session generation"):
        await verify_token(token, secret=secret, ctx=user_ctx)


@pytest.mark.asyncio
async def test_active_agent_jwt_accepted(user_ctx: MagicMock) -> None:
    """Active owned and ownerless agent JWTs pass verification when active in ``agents``."""
    secret = "a" * 32  # pragma: allowlist secret
    owned_token = issue_jwt(
        claims={
            "sub": "agnt_1",
            "actor_type": "agent",
            "parent_actor_id": "usr_1",
            "scopes": ["apis:read"],
        },
        secret=secret,
        ttl_seconds=3600,
    )
    unowned_token = issue_jwt(
        claims={"sub": "agnt_unowned", "actor_type": "agent", "scopes": ["apis:read"]},
        secret=secret,
        ttl_seconds=3600,
    )

    owned_identity = await verify_token(owned_token, secret=secret, ctx=user_ctx)
    unowned_identity = await verify_token(unowned_token, secret=secret, ctx=user_ctx)

    assert owned_identity.sub == "agnt_1"
    assert owned_identity.parent_actor_id == "usr_1"
    assert owned_identity.permissions == ["apis:read"]
    assert unowned_identity.sub == "agnt_unowned"
    assert unowned_identity.parent_actor_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_status", ["missing", "disabled", "archived", "pending", "rejected"])
async def test_inactive_or_archived_agent_jwt_rejected(
    user_ctx: MagicMock, agent_status: str
) -> None:
    """Missing, disabled, archived, pending, or rejected ``ActorType.AGENT`` JWTs are refused."""
    secret = "a" * 32  # pragma: allowlist secret
    sub = "agnt_missing" if agent_status == "missing" else "agnt_1"
    if agent_status != "missing":
        async with user_ctx.admin_db.transaction() as session:
            await session.execute(
                text("UPDATE agents SET status = :status WHERE id = 'agnt_1'"),
                {"status": agent_status},
            )
    token = issue_jwt(
        claims={
            "sub": sub,
            "actor_type": "agent",
            "parent_actor_id": "usr_1",
            "scopes": ["apis:read"],
        },
        secret=secret,
        ttl_seconds=3600,
    )

    with pytest.raises(InvalidTokenError):
        await verify_token(token, secret=secret, ctx=user_ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_state", ["disabled", "deleted"])
async def test_agent_jwt_with_inactive_or_deleted_owner_rejected(
    user_ctx: MagicMock, owner_state: str
) -> None:
    """An active agent whose human owner is disabled or deleted cannot authenticate via JWT."""
    async with user_ctx.admin_db.transaction() as session:
        if owner_state == "disabled":
            await session.execute(text("UPDATE users SET active = 0 WHERE id = 'usr_1'"))
        else:
            await session.execute(
                text("UPDATE users SET deleted_at = CURRENT_TIMESTAMP WHERE id = 'usr_1'")
            )
    secret = "a" * 32  # pragma: allowlist secret
    # Omit parent_actor_id to prove owner_id from the agents row itself is enforced too.
    token = issue_jwt(
        claims={"sub": "agnt_1", "actor_type": "agent", "scopes": ["apis:read"]},
        secret=secret,
        ttl_seconds=3600,
    )

    with pytest.raises(InvalidTokenError):
        await verify_token(token, secret=secret, ctx=user_ctx)


@pytest.mark.asyncio
async def test_agent_jwt_with_mismatched_parent_actor_id_rejected(user_ctx: MagicMock) -> None:
    """An agent JWT claiming a ``parent_actor_id`` different from ``agents.owner_id`` is refused."""
    secret = "a" * 32  # pragma: allowlist secret
    token = issue_jwt(
        claims={
            "sub": "agnt_1",
            "actor_type": "agent",
            "parent_actor_id": "usr_2",
            "scopes": ["apis:read"],
        },
        secret=secret,
        ttl_seconds=3600,
    )

    with pytest.raises(InvalidTokenError):
        await verify_token(token, secret=secret, ctx=user_ctx)


@pytest.mark.asyncio
async def test_agent_jwt_skips_admin_db_when_disallowed(mock_ctx: MagicMock) -> None:
    """When ``ctx.is_db_allowed('admin')`` is False, agent claim verification skips DB lookup."""
    mock_ctx.is_db_allowed.return_value = False
    secret = hashlib.sha256(b"unit-stub-secret").hexdigest()
    token = issue_jwt(
        claims={"sub": "agnt_stub", "actor_type": "agent", "permissions": ["apis:read"]},
        secret=secret,
        ttl_seconds=3600,
    )

    identity = await verify_token(token, secret=secret, ctx=mock_ctx)
    assert identity.sub == "agnt_stub"
