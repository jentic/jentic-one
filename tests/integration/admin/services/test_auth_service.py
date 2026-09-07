"""Integration tests for AuthService against real PostgreSQL."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from sqlalchemy import delete

from jentic_one.admin.core.permissions import ORG_ADMIN
from jentic_one.admin.core.schema.invite_tokens import InviteToken
from jentic_one.admin.core.schema.setup_sentinel import SETUP_SENTINEL_ID, SetupSentinel
from jentic_one.admin.core.schema.user_permission_grants import UserPermissionGrant
from jentic_one.admin.core.schema.user_secrets import UserSecret
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import (
    UserPermissionGrantRepository,
    UserRepository,
    UserSecretRepository,
)
from jentic_one.admin.services._support.passwords import hash_password
from jentic_one.admin.services._support.tokens import issue_jwt
from jentic_one.admin.services.auth_service import AuthService
from jentic_one.admin.services.errors import (
    AccountLockedError,
    InvalidCredentialsError,
    InvalidInputError,
    SessionExpiredError,
    SetupAlreadyCompleteError,
    UserEmailNotFoundError,
)
from jentic_one.admin.services.schemas.auth import ChangePasswordPayload, LoginPayload
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import InviteState

pytestmark = pytest.mark.integration

# Test-only credentials for the first-run bootstrap flow. Defined once with an
# allowlist pragma so the secrets scanner doesn't flag every call site.
_BOOTSTRAP_PW = "a-strong-passw0rd"  # pragma: allowlist secret
_OTHER_PW = "another-passw0rd"  # pragma: allowlist secret
_SHORT_PW = "short"  # pragma: allowlist secret
_ROTATED_PW = "post-reset-passw0rd"  # pragma: allowlist secret


@pytest.fixture()
async def auth_user(
    integration_context: Context,
) -> AsyncGenerator[tuple[str, str], None]:
    """Create a user with password and return (user_id, email)."""
    ctx = integration_context
    email = "auth-test@example.com"
    async with ctx.admin_db.session() as session:
        user = await UserRepository.create(
            session,
            email=email,
            first_name="Auth",
            last_name="Test",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        await UserSecretRepository.create(
            session,
            user_id=user.id,
            password_hash=hash_password("correct-password"),
            created_by="usr_test",
        )
        await UserPermissionGrantRepository.set_permissions(
            session, user.id, permissions={"users:read"}, granted_by=None, created_by="usr_test"
        )
        await session.commit()
    yield (user.id, email)

    # Cleanup
    async with ctx.admin_db.session() as session:
        await session.execute(delete(InviteToken).where(InviteToken.user_id == user.id))
        await session.execute(
            delete(UserPermissionGrant).where(UserPermissionGrant.user_id == user.id)
        )
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user.id))
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()


async def test_login_success(integration_context: Context, auth_user: tuple[str, str]) -> None:
    _, email = auth_user
    service = AuthService(integration_context)
    result = await service.login(LoginPayload(email=email, password="correct-password"))
    assert result.access_token
    assert result.token_type == "bearer"
    assert result.expires_in > 0


async def test_login_wrong_password(
    integration_context: Context, auth_user: tuple[str, str]
) -> None:
    _, email = auth_user
    service = AuthService(integration_context)
    with pytest.raises(InvalidCredentialsError):
        await service.login(LoginPayload(email=email, password="wrong-password"))


async def test_login_unknown_email(integration_context: Context) -> None:
    service = AuthService(integration_context)
    with pytest.raises(InvalidCredentialsError):
        await service.login(LoginPayload(email="nobody@nowhere.com", password="any"))


async def test_login_disabled_user(integration_context: Context) -> None:
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        user = await UserRepository.create(
            session,
            email="disabled-auth@test.local",
            first_name="Disabled",
            last_name="User",
            active=False,
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        await UserSecretRepository.create(
            session,
            user_id=user.id,
            password_hash=hash_password("some-pass"),
            created_by="usr_test",
        )
        await session.commit()

    service = AuthService(ctx)
    with pytest.raises(InvalidCredentialsError):
        await service.login(LoginPayload(email="disabled-auth@test.local", password="some-pass"))

    # Cleanup
    async with ctx.admin_db.session() as session:
        await session.execute(delete(UserSecret).where(UserSecret.user_id == user.id))
        await session.execute(delete(User).where(User.id == user.id))
        await session.commit()


async def test_login_locked_account(
    integration_context: Context, auth_user: tuple[str, str]
) -> None:
    user_id, email = auth_user
    ctx = integration_context

    # Lock the account
    async with ctx.admin_db.session() as session:
        await UserSecretRepository.lock_until(
            session, user_id, locked_until=datetime.now(UTC) + timedelta(minutes=15)
        )
        await session.commit()

    service = AuthService(ctx)
    with pytest.raises(AccountLockedError):
        await service.login(LoginPayload(email=email, password="correct-password"))

    # Unlock for cleanup
    async with ctx.admin_db.session() as session:
        await UserSecretRepository.lock_until(
            session, user_id, locked_until=datetime.now(UTC) - timedelta(minutes=1)
        )
        await session.commit()


# ── authenticate (credential check without minting a token) ────────────────


async def test_authenticate_success_returns_user_id(
    integration_context: Context, auth_user: tuple[str, str]
) -> None:
    user_id, email = auth_user
    service = AuthService(integration_context)
    result = await service.authenticate(LoginPayload(email=email, password="correct-password"))
    assert result == user_id


async def test_authenticate_wrong_password_increments_failed_count(
    integration_context: Context, auth_user: tuple[str, str]
) -> None:
    user_id, email = auth_user
    service = AuthService(integration_context)
    with pytest.raises(InvalidCredentialsError):
        await service.authenticate(LoginPayload(email=email, password="wrong-password"))

    async with integration_context.admin_db.session() as session:
        secret = await UserSecretRepository.get_by_user_id(session, user_id)
        assert secret is not None
        assert secret.failed_login_count == 1

    # A subsequent success resets the counter.
    await service.authenticate(LoginPayload(email=email, password="correct-password"))
    async with integration_context.admin_db.session() as session:
        secret = await UserSecretRepository.get_by_user_id(session, user_id)
        assert secret is not None
        assert secret.failed_login_count == 0


async def test_authenticate_lockout_at_threshold(
    integration_context: Context, auth_user: tuple[str, str]
) -> None:
    """Enough consecutive failures lock the account; the lock then rejects."""
    user_id, email = auth_user
    ctx = integration_context
    service = AuthService(ctx)

    threshold = ctx.config.admin.auth.failed_login_lockout_threshold
    for _ in range(threshold):
        with pytest.raises(InvalidCredentialsError):
            await service.authenticate(LoginPayload(email=email, password="wrong-password"))

    async with ctx.admin_db.session() as session:
        secret = await UserSecretRepository.get_by_user_id(session, user_id)
        assert secret is not None
        assert secret.locked_until is not None

    # Even the correct password is now refused while the lock holds.
    with pytest.raises(AccountLockedError):
        await service.authenticate(LoginPayload(email=email, password="correct-password"))

    # Unlock for cleanup
    async with ctx.admin_db.session() as session:
        await UserSecretRepository.unlock(session, user_id)
        await session.commit()


async def test_change_password_success(
    integration_context: Context, auth_user: tuple[str, str]
) -> None:
    user_id, email = auth_user
    service = AuthService(integration_context)
    await service.change_own_password(
        ChangePasswordPayload(current_password="correct-password", new_password="new-secure-pw"),
        identity=Identity(sub=user_id, email="test@local"),
    )

    # Verify new password works
    result = await service.login(LoginPayload(email=email, password="new-secure-pw"))
    assert result.access_token

    # Reset to original for other tests
    async with integration_context.admin_db.session() as session:
        await UserSecretRepository.set_password_hash(
            session, user_id, password_hash=hash_password("correct-password"), created_by="usr_test"
        )
        await session.commit()


async def test_change_password_wrong_current(
    integration_context: Context, auth_user: tuple[str, str]
) -> None:
    user_id, _ = auth_user
    service = AuthService(integration_context)
    with pytest.raises(InvalidCredentialsError):
        await service.change_own_password(
            ChangePasswordPayload(current_password="wrong", new_password="new-secure-pw"),
            identity=Identity(sub=user_id, email="test@local"),
        )


async def test_change_password_too_short(
    integration_context: Context, auth_user: tuple[str, str]
) -> None:
    user_id, _ = auth_user
    service = AuthService(integration_context)
    with pytest.raises(InvalidInputError):
        await service.change_own_password(
            ChangePasswordPayload(current_password="x", new_password="short"),
            identity=Identity(sub=user_id, email="test@local"),
        )


# ── Operator password reset (temp password + forced change) ────────────────


async def test_reset_password_sets_temp_and_forces_change(
    integration_context: Context, auth_user: tuple[str, str]
) -> None:
    """Reset sets the temp credential, flips the gate, and logs the user in gated."""
    user_id, email = auth_user
    service = AuthService(integration_context)

    returned_id = await service.reset_password(email=email, temporary_password=_OTHER_PW)
    assert returned_id == user_id

    # The temporary password authenticates, and login reports the gate is set.
    login = await service.login(LoginPayload(email=email, password=_OTHER_PW))
    assert login.access_token
    assert login.must_change_password is True

    # The gate is persisted on the user row.
    async with integration_context.admin_db.session() as session:
        user = await UserRepository.get_by_id(session, user_id)
        assert user is not None
        assert user.must_change_password is True

    # The user can rotate out of the gate themselves, clearing must_change_password.
    bundle = await service.change_own_password(
        ChangePasswordPayload(current_password=_OTHER_PW, new_password=_ROTATED_PW),
        identity=Identity(sub=user_id, email=email),
    )
    assert bundle.must_change_password is False

    # Reset password fixture state for other tests sharing the DB.
    async with integration_context.admin_db.session() as session:
        await UserSecretRepository.set_password_hash(
            session, user_id, password_hash=hash_password("correct-password"), created_by="usr_test"
        )
        await session.commit()


async def test_reset_password_clears_lockout(
    integration_context: Context, auth_user: tuple[str, str]
) -> None:
    """A user locked out by failed logins is freed by the reset."""
    user_id, email = auth_user
    ctx = integration_context

    async with ctx.admin_db.session() as session:
        await UserSecretRepository.lock_until(
            session, user_id, locked_until=datetime.now(UTC) + timedelta(minutes=15)
        )
        await session.commit()

    service = AuthService(ctx)
    await service.reset_password(email=email, temporary_password=_OTHER_PW)

    # No longer locked: login with the temp succeeds instead of raising.
    login = await service.login(LoginPayload(email=email, password=_OTHER_PW))
    assert login.access_token

    async with ctx.admin_db.session() as session:
        secret = await UserSecretRepository.get_by_user_id(session, user_id)
        assert secret is not None
        assert secret.locked_until is None
        assert secret.failed_login_count == 0
        await UserSecretRepository.set_password_hash(
            session, user_id, password_hash=hash_password("correct-password"), created_by="usr_test"
        )
        await session.commit()


async def test_reset_password_unknown_email(integration_context: Context) -> None:
    service = AuthService(integration_context)
    with pytest.raises(UserEmailNotFoundError):
        await service.reset_password(email="nobody@nowhere.com", temporary_password=_OTHER_PW)


async def test_reset_password_rejects_short_password(
    integration_context: Context, auth_user: tuple[str, str]
) -> None:
    _, email = auth_user
    service = AuthService(integration_context)
    with pytest.raises(InvalidInputError):
        await service.reset_password(email=email, temporary_password=_SHORT_PW)


async def test_verify_token_valid(integration_context: Context, auth_user: tuple[str, str]) -> None:
    ctx = integration_context

    user_id, _ = auth_user
    # auth_user fixture already grants users:read via the repo

    # Issue the JWT without embedding permissions directly
    claims = {
        "sub": user_id,
        "email": "verify@test.com",
        "actor_type": "user",
        "scopes": ["*"],
        "must_change_password": False,
    }
    token = issue_jwt(claims, ctx.config.admin.auth.jwt_secret.get_secret_value(), 3600)

    # 3. Verify
    service = AuthService(ctx)
    identity = await service.verify_token(token)
    assert identity.sub == user_id
    assert identity.email == "verify@test.com"
    assert "users:read" in identity.permissions


async def test_verify_token_expired(integration_context: Context) -> None:
    ctx = integration_context
    claims = {"sub": "usr_test", "email": "x@x.com", "permissions": []}
    token = issue_jwt(claims, ctx.config.admin.auth.jwt_secret.get_secret_value(), -1)
    service = AuthService(ctx)
    with pytest.raises(jwt.ExpiredSignatureError):
        await service.verify_token(token)


async def test_verify_token_invalid(integration_context: Context) -> None:
    service = AuthService(integration_context)
    with pytest.raises(jwt.InvalidTokenError):
        await service.verify_token("not-a-valid-token")


# ── First-run bootstrap (no-credential setup) ──────────────────────────────


@pytest.fixture()
async def _empty_users(integration_context: Context) -> AsyncGenerator[None, None]:
    """Guarantee an empty users table around the test (first-run state).

    bootstrap_admin only succeeds while the table is empty AND the setup sentinel
    is absent, so we clear all user rows (and dependents) plus the singleton
    sentinel before yielding and again after, to avoid leaking the bootstrapped
    admin (or a stale sentinel that would force a spurious 410) into other tests
    sharing the integration DB.
    """

    async def _wipe() -> None:
        async with integration_context.admin_db.session() as session:
            await session.execute(delete(InviteToken))
            await session.execute(delete(UserPermissionGrant))
            await session.execute(delete(UserSecret))
            await session.execute(delete(User))
            await session.execute(delete(SetupSentinel))
            await session.commit()

    await _wipe()
    yield
    await _wipe()


async def test_bootstrap_admin_creates_first_admin(
    integration_context: Context, _empty_users: None
) -> None:
    ctx = integration_context
    service = AuthService(ctx)

    bundle = await service.bootstrap_admin(email="founder@test.local", password=_BOOTSTRAP_PW)

    # Auto-login: a usable token with the change-password gate already cleared.
    assert bundle.access_token
    assert bundle.must_change_password is False

    # The token carries org:admin and resolves to the new user.
    identity = await service.verify_token(bundle.access_token)
    assert ORG_ADMIN in identity.permissions

    # The new password actually authenticates.
    login = await service.login(LoginPayload(email="founder@test.local", password=_BOOTSTRAP_PW))
    assert login.access_token

    # Exactly one user now exists.
    async with ctx.admin_db.session() as session:
        assert await UserRepository.count(session) == 1


async def test_bootstrap_admin_self_closes_after_first_user(
    integration_context: Context, _empty_users: None
) -> None:
    service = AuthService(integration_context)
    await service.bootstrap_admin(email="first@test.local", password=_BOOTSTRAP_PW)

    # A second attempt with a DIFFERENT email (e.g. an agent racing the operator)
    # is rejected: the singleton setup sentinel — not the unique email index — is
    # what closes the endpoint, so a distinct-email land-grab cannot create a
    # second org:admin.
    with pytest.raises(SetupAlreadyCompleteError):
        await service.bootstrap_admin(email="second@test.local", password=_OTHER_PW)

    # Still exactly one user, and the singleton sentinel is the only lock row.
    async with integration_context.admin_db.session() as session:
        assert await UserRepository.count(session) == 1
        sentinel = await session.get(SetupSentinel, SETUP_SENTINEL_ID)
        assert sentinel is not None


async def test_bootstrap_admin_rejects_short_password(
    integration_context: Context, _empty_users: None
) -> None:
    service = AuthService(integration_context)
    with pytest.raises(InvalidInputError):
        await service.bootstrap_admin(email="founder@test.local", password=_SHORT_PW)

    # Nothing was created — setup is still required.
    async with integration_context.admin_db.session() as session:
        assert await UserRepository.count(session) == 0


async def test_bootstrap_admin_sentinel_collision_maps_to_410(
    integration_context: Context, _empty_users: None
) -> None:
    """A pre-existing sentinel (no users yet) trips the PK branch, not count().

    This is the branch the ``setup_sentinel`` table exists for: the users table
    is still empty (so the ``count() == 0`` fast-path is *passed*), but the
    singleton sentinel row already exists, so the in-transaction insert collides
    on the primary key and must surface as a clean ``SetupAlreadyCompleteError``
    (410) rather than a raw IntegrityError / 500.
    """
    ctx = integration_context
    # Plant the sentinel without creating any user — simulates a concurrent
    # caller that claimed the lock but whose user row isn't visible yet.
    async with ctx.admin_db.session() as session:
        session.add(SetupSentinel(id=SETUP_SENTINEL_ID))
        await session.commit()

    service = AuthService(ctx)
    with pytest.raises(SetupAlreadyCompleteError):
        await service.bootstrap_admin(email="late@test.local", password=_BOOTSTRAP_PW)

    # The losing attempt created no user.
    async with ctx.admin_db.session() as session:
        assert await UserRepository.count(session) == 0


async def test_bootstrap_admin_concurrent_distinct_emails_one_winner(
    integration_context: Context, _empty_users: None
) -> None:
    """Two overlapping first-admin attempts with DIFFERENT emails → one winner.

    The ``count() == 0`` check cannot serialize this under READ COMMITTED (no
    range lock), and the unique email index only covers same-email races. The
    singleton sentinel is what forces exactly one to win; the loser must get a
    clean ``SetupAlreadyCompleteError`` (410), never a second org:admin.

    Run the two bootstraps concurrently so their transactions genuinely overlap
    rather than the sequential ``count()>0`` fast-path.
    """
    ctx = integration_context
    service = AuthService(ctx)

    results = await asyncio.gather(
        service.bootstrap_admin(email="racer-a@test.local", password=_BOOTSTRAP_PW),
        service.bootstrap_admin(email="racer-b@test.local", password=_OTHER_PW),
        return_exceptions=True,
    )

    winners = [r for r in results if not isinstance(r, BaseException)]
    losers = [r for r in results if isinstance(r, BaseException)]

    assert len(winners) == 1, f"expected exactly one winner, got {results!r}"
    assert len(losers) == 1
    # The loser is rejected cleanly (410), not via a leaked IntegrityError/500.
    assert isinstance(losers[0], SetupAlreadyCompleteError), f"loser was {losers[0]!r}"

    # Exactly one admin exists.
    async with ctx.admin_db.session() as session:
        assert await UserRepository.count(session) == 1


async def test_change_password_remints_token_clearing_gate(
    integration_context: Context, auth_user: tuple[str, str]
) -> None:
    """change_own_password returns a fresh token with the gate cleared."""
    user_id, _ = auth_user
    ctx = integration_context

    # Force the must_change_password gate up, like a freshly invited user.
    async with ctx.admin_db.session() as session:
        await UserRepository.update(session, user_id, must_change_password=True)
        await session.commit()

    service = AuthService(ctx)
    bundle = await service.change_own_password(
        ChangePasswordPayload(current_password="correct-password", new_password="a-new-strong-pw"),
        identity=Identity(sub=user_id, email="test@local"),
    )

    # The re-minted bundle and its decoded claim both report the gate cleared.
    assert bundle.must_change_password is False
    decoded = jwt.decode(
        bundle.access_token,
        ctx.config.admin.auth.jwt_secret.get_secret_value(),
        algorithms=["HS256"],
    )
    assert decoded["must_change_password"] is False

    # Reset password + gate for other tests sharing the fixture's user.
    async with ctx.admin_db.session() as session:
        await UserSecretRepository.set_password_hash(
            session, user_id, password_hash=hash_password("correct-password"), created_by="usr_test"
        )
        await UserRepository.update(session, user_id, must_change_password=False)
        await session.commit()


# ── Session refresh (sliding session, absolute window) ─────────────────────


def _decode(ctx: Context, token: str) -> dict[str, Any]:
    return jwt.decode(
        token, ctx.config.admin.auth.jwt_secret.get_secret_value(), algorithms=["HS256"]
    )


async def test_refresh_remints_with_fresh_permissions(
    integration_context: Context, auth_user: tuple[str, str]
) -> None:
    """Refresh re-reads grants from the DB — permission changes take effect."""
    user_id, email = auth_user
    ctx = integration_context
    service = AuthService(ctx)

    login = await service.login(LoginPayload(email=email, password="correct-password"))
    assert "users:write" not in _decode(ctx, login.access_token)["permissions"]

    # Widen the grants after the original mint.
    async with ctx.admin_db.session() as session:
        await UserPermissionGrantRepository.set_permissions(
            session,
            user_id,
            permissions={"users:read", "users:write"},
            granted_by=None,
            created_by="usr_test",
        )
        await session.commit()

    refreshed = await service.refresh(login.access_token)
    claims = _decode(ctx, refreshed.access_token)
    assert "users:write" in claims["permissions"]
    # The absolute window still anchors on the original login.
    assert claims["auth_time"] == _decode(ctx, login.access_token)["auth_time"]


async def test_refresh_legacy_token_without_auth_time_rejected(
    integration_context: Context, auth_user: tuple[str, str]
) -> None:
    """Fail closed: pre-upgrade tokens (no auth_time) cannot refresh.

    They expire at their natural TTL and the user signs in once more —
    preferable to inferring a session window the token never declared.
    """
    user_id, _ = auth_user
    ctx = integration_context
    legacy_claims = {
        "sub": user_id,
        "email": "legacy@test.local",
        "actor_type": "user",
        "permissions": ["users:read"],
        "must_change_password": False,
        # No auth_time — pre-upgrade token shape.
    }
    token = issue_jwt(legacy_claims, ctx.config.admin.auth.jwt_secret.get_secret_value(), 3600)

    service = AuthService(ctx)
    with pytest.raises(InvalidCredentialsError):
        await service.refresh(token)


async def test_refresh_past_absolute_window(
    integration_context: Context, auth_user: tuple[str, str]
) -> None:
    user_id, _ = auth_user
    ctx = integration_context
    stale = int(datetime.now(UTC).timestamp()) - ctx.config.admin.auth.session_ttl_seconds - 60
    claims = {
        "sub": user_id,
        "email": "stale@test.local",
        "actor_type": "user",
        "permissions": ["users:read"],
        "must_change_password": False,
        "auth_time": stale,
    }
    token = issue_jwt(claims, ctx.config.admin.auth.jwt_secret.get_secret_value(), 3600)

    service = AuthService(ctx)
    with pytest.raises(SessionExpiredError):
        await service.refresh(token)


async def test_refresh_inactive_user_rejected(
    integration_context: Context, auth_user: tuple[str, str]
) -> None:
    """Deactivation takes effect at the next refresh, not just the next login."""
    user_id, email = auth_user
    ctx = integration_context
    service = AuthService(ctx)
    login = await service.login(LoginPayload(email=email, password="correct-password"))

    async with ctx.admin_db.session() as session:
        await UserRepository.update(session, user_id, active=False)
        await session.commit()

    with pytest.raises(InvalidCredentialsError):
        await service.refresh(login.access_token)


async def test_refresh_non_user_actor_rejected(
    integration_context: Context, auth_user: tuple[str, str]
) -> None:
    user_id, _ = auth_user
    ctx = integration_context
    claims = {
        "sub": user_id,
        "email": "agent@test.local",
        "actor_type": "agent",
        "permissions": [],
        "must_change_password": False,
    }
    token = issue_jwt(claims, ctx.config.admin.auth.jwt_secret.get_secret_value(), 3600)

    service = AuthService(ctx)
    with pytest.raises(InvalidCredentialsError):
        await service.refresh(token)


async def test_refresh_missing_actor_type_rejected(
    integration_context: Context, auth_user: tuple[str, str]
) -> None:
    """Fail closed: a token that doesn't declare actor_type is not refreshable."""
    user_id, _ = auth_user
    ctx = integration_context
    claims = {
        "sub": user_id,
        "email": "noactortype@test.local",
        "permissions": [],
        "must_change_password": False,
    }
    token = issue_jwt(claims, ctx.config.admin.auth.jwt_secret.get_secret_value(), 3600)

    service = AuthService(ctx)
    with pytest.raises(InvalidCredentialsError):
        await service.refresh(token)


async def test_refresh_unknown_actor_type_rejected(
    integration_context: Context, auth_user: tuple[str, str]
) -> None:
    """An unrecognised actor_type is refused with a 401-mapped error, not a 500."""
    user_id, _ = auth_user
    ctx = integration_context
    claims = {
        "sub": user_id,
        "email": "junkactortype@test.local",
        "actor_type": "definitely-not-a-real-actor",
        "permissions": [],
        "must_change_password": False,
    }
    token = issue_jwt(claims, ctx.config.admin.auth.jwt_secret.get_secret_value(), 3600)

    service = AuthService(ctx)
    with pytest.raises(InvalidCredentialsError):
        await service.refresh(token)


async def test_refresh_opaque_token_rejected(integration_context: Context) -> None:
    """Opaque platform-actor tokens (at_…) are not JWTs and cannot refresh."""
    service = AuthService(integration_context)
    with pytest.raises(InvalidCredentialsError):
        await service.refresh("at_notactuallyajwt")


async def test_refresh_expired_jwt_rejected(
    integration_context: Context, auth_user: tuple[str, str]
) -> None:
    """Defence in depth: an expired signature is refused inside the service too."""
    user_id, _ = auth_user
    ctx = integration_context
    claims = {
        "sub": user_id,
        "email": "expired@test.local",
        "actor_type": "user",
        "permissions": [],
        "must_change_password": False,
    }
    token = issue_jwt(claims, ctx.config.admin.auth.jwt_secret.get_secret_value(), -10)

    service = AuthService(ctx)
    with pytest.raises(InvalidCredentialsError):
        await service.refresh(token)
