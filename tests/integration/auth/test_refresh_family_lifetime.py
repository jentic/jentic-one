"""Integration tests for OAuth refresh-token family absolute lifetime."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from jentic_one.admin.core.schema.refresh_tokens import RefreshToken
from jentic_one.admin.repos import AccessTokenRepository, RefreshTokenRepository
from jentic_one.auth.services.errors import InvalidGrantError
from jentic_one.auth.services.token_service import TokenService, _hash_token
from jentic_one.shared.auth.permission_catalog import APIS_READ
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from tests.integration.auth.seeds import seed_password_user, seed_permissions

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_refresh_family_past_max_age_revokes_family_and_refuses_rotation(
    local_login_ctx: Context,
    clean_grants: None,
    clean_user_secrets: None,
) -> None:
    """A refresh token whose family is older than refresh_family_max_age_seconds
    introspects as inactive and, on refresh, revokes the entire token family and
    raises InvalidGrantError."""
    ctx = local_login_ctx
    max_age = ctx.config.auth.refresh_family_max_age_seconds
    assert isinstance(max_age, int)

    user_id, _ = await seed_password_user(ctx, "usr_family_expired")
    await seed_permissions(ctx, user_id, [APIS_READ])

    token_svc = TokenService(ctx)
    access_token, refresh_token = await token_svc.issue_pair(
        user_id, ActorType.USER, ["openid", APIS_READ]
    )

    expired_family_anchor = datetime.now(UTC) - timedelta(seconds=max_age + 1)
    async with ctx.admin_db.transaction() as session:
        rt_row = await RefreshTokenRepository.get_by_hash(session, _hash_token(refresh_token))
        assert rt_row is not None
        assert rt_row.family_issued_at is not None
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.id == rt_row.id)
            .values(family_issued_at=expired_family_anchor)
        )

    assert (await token_svc.introspect(refresh_token))["active"] is False

    with pytest.raises(InvalidGrantError, match="refresh token family expired"):
        await token_svc.refresh(refresh_token)

    async with ctx.admin_db.session() as session:
        at_after = await AccessTokenRepository.get_by_hash(session, _hash_token(access_token))
        rt_after = await RefreshTokenRepository.get_by_hash(session, _hash_token(refresh_token))
        assert at_after is not None and at_after.revoked_at is not None
        assert rt_after is not None and rt_after.revoked_at is not None

    resolved_at = await token_svc.resolve_access_token(access_token)
    assert resolved_at is None or resolved_at.active is False
    assert (await token_svc.introspect(access_token))["active"] is False


@pytest.mark.asyncio
async def test_refresh_family_near_max_age_rotates_and_caps_expires_at(
    local_login_ctx: Context,
    clean_grants: None,
    clean_user_secrets: None,
) -> None:
    """A family 60 seconds before max_age still rotates, preserves family_issued_at,
    and caps the new refresh token's expires_at at family_issued_at + max_age."""
    ctx = local_login_ctx
    max_age = ctx.config.auth.refresh_family_max_age_seconds
    assert isinstance(max_age, int)

    user_id, _ = await seed_password_user(ctx, "usr_family_near_cap")
    await seed_permissions(ctx, user_id, [APIS_READ])

    token_svc = TokenService(ctx)
    _access_token, refresh_token = await token_svc.issue_pair(
        user_id, ActorType.USER, ["openid", APIS_READ]
    )

    near_cap_anchor = (datetime.now(UTC) - timedelta(seconds=max_age - 60)).replace(microsecond=0)
    async with ctx.admin_db.transaction() as session:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == _hash_token(refresh_token))
            .values(family_issued_at=near_cap_anchor)
        )

    assert (await token_svc.introspect(refresh_token))["active"] is True

    new_at, new_rt, _scopes = await token_svc.refresh(refresh_token)
    assert new_at.startswith("at_")
    assert new_rt.startswith("rt_")

    cap_deadline = near_cap_anchor + timedelta(seconds=max_age)
    async with ctx.admin_db.session() as session:
        rotated_row = await RefreshTokenRepository.get_by_hash(session, _hash_token(new_rt))
        assert rotated_row is not None
        assert rotated_row.family_issued_at == near_cap_anchor
        assert rotated_row.expires_at <= cap_deadline


@pytest.mark.asyncio
async def test_legacy_refresh_token_with_null_family_issued_at_rotates_and_stamps_anchor(
    local_login_ctx: Context,
    clean_grants: None,
    clean_user_secrets: None,
) -> None:
    """A pre-migration row with NULL family_issued_at falls back to created_at:
    within max_age it rotates and stamps family_issued_at = created_at; past
    max_age it fails closed and revokes the family."""
    ctx = local_login_ctx
    max_age = ctx.config.auth.refresh_family_max_age_seconds
    assert isinstance(max_age, int)

    user_id, _ = await seed_password_user(ctx, "usr_family_legacy")
    await seed_permissions(ctx, user_id, [APIS_READ])

    token_svc = TokenService(ctx)
    _at1, rt1 = await token_svc.issue_pair(user_id, ActorType.USER, ["openid", APIS_READ])

    legacy_created_at = (datetime.now(UTC) - timedelta(days=10)).replace(microsecond=0)
    async with ctx.admin_db.transaction() as session:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == _hash_token(rt1))
            .values(family_issued_at=None, created_at=legacy_created_at)
        )

    _rotated_at, rotated_rt, _ = await token_svc.refresh(rt1)
    async with ctx.admin_db.session() as session:
        rotated_row = await RefreshTokenRepository.get_by_hash(session, _hash_token(rotated_rt))
        assert rotated_row is not None
        assert rotated_row.family_issued_at == legacy_created_at

    # Legacy row whose created_at is already past max_age is rejected and revoked.
    at2, rt2 = await token_svc.issue_pair(user_id, ActorType.USER, ["openid", APIS_READ])
    expired_created_at = datetime.now(UTC) - timedelta(seconds=max_age + 1)
    async with ctx.admin_db.transaction() as session:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == _hash_token(rt2))
            .values(family_issued_at=None, created_at=expired_created_at)
        )

    assert (await token_svc.introspect(rt2))["active"] is False
    with pytest.raises(InvalidGrantError, match="refresh token family expired"):
        await token_svc.refresh(rt2)

    async with ctx.admin_db.session() as session:
        at2_after = await AccessTokenRepository.get_by_hash(session, _hash_token(at2))
        assert at2_after is not None and at2_after.revoked_at is not None


@pytest.mark.asyncio
async def test_refresh_family_max_age_none_disables_lifetime_cap(
    local_login_ctx: Context,
    clean_grants: None,
    clean_user_secrets: None,
) -> None:
    """Setting auth.refresh_family_max_age_seconds to None disables the absolute
    family lifetime cap while still carrying family_issued_at across rotations."""
    ctx = local_login_ctx
    assert hasattr(ctx.config.auth, "refresh_family_max_age_seconds")
    prior_max_age = ctx.config.auth.refresh_family_max_age_seconds
    ctx.config.auth.refresh_family_max_age_seconds = None
    try:
        user_id, _ = await seed_password_user(ctx, "usr_family_uncapped")
        await seed_permissions(ctx, user_id, [APIS_READ])

        token_svc = TokenService(ctx)
        _at, rt = await token_svc.issue_pair(user_id, ActorType.USER, ["openid", APIS_READ])

        ancient_anchor = (datetime.now(UTC) - timedelta(days=365)).replace(microsecond=0)
        async with ctx.admin_db.transaction() as session:
            await session.execute(
                update(RefreshToken)
                .where(RefreshToken.token_hash == _hash_token(rt))
                .values(family_issued_at=ancient_anchor)
            )

        assert (await token_svc.introspect(rt))["active"] is True
        before_rotate = datetime.now(UTC)
        _new_at, new_rt, _ = await token_svc.refresh(rt)

        async with ctx.admin_db.session() as session:
            rotated_row = await RefreshTokenRepository.get_by_hash(session, _hash_token(new_rt))
            assert rotated_row is not None
            assert rotated_row.family_issued_at == ancient_anchor
            assert rotated_row.expires_at >= before_rotate + timedelta(
                seconds=ctx.config.auth.refresh_ttl_seconds - 5
            )
    finally:
        ctx.config.auth.refresh_family_max_age_seconds = prior_max_age
