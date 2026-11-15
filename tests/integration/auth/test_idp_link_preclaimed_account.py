"""Integration tests for IdP email auto-linking against pre-created accounts."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.external_identities import ExternalIdentity
from jentic_one.admin.core.schema.invite_tokens import InviteToken
from jentic_one.admin.core.schema.user_permission_grants import UserPermissionGrant
from jentic_one.admin.core.schema.user_secrets import UserSecret
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import (
    InviteTokenRepository,
    UserPermissionGrantRepository,
    UserRepository,
    UserSecretRepository,
)
from jentic_one.admin.services._support.tokens import InvalidTokenError
from jentic_one.admin.services.auth_service import AuthService
from jentic_one.admin.services.errors import (
    InvalidCredentialsError,
    InviteTokenAlreadyRedeemedError,
)
from jentic_one.admin.services.invite_service import InviteService
from jentic_one.admin.services.schemas.auth import LoginPayload
from jentic_one.admin.services.schemas.users import UserCreatePayload
from jentic_one.admin.services.user_service import UserService
from jentic_one.auth.core.idp.adapter import IdpClaims
from jentic_one.auth.services.authorize_service import AuthorizeService
from jentic_one.auth.services.errors import InvalidGrantError
from jentic_one.shared.audit import AuditAction, AuditTargetType
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import IdpConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models import InviteState

pytestmark = pytest.mark.integration

_IDP_ISSUER = "https://idp.link-test.local"
_IDP_PROVIDER = "oidc"
_INITIAL_PASSWORD = "initial-password-1234"  # pragma: allowlist secret


class _FakeIdpAdapter:
    """Minimal IdpAdapter double providing the configured issuer."""

    issuer = _IDP_ISSUER

    def authorize_url(
        self,
        *,
        state: str,
        nonce: str,
        redirect_uri: str,
        code_challenge: str | None = None,
    ) -> str:
        return f"{_IDP_ISSUER}/authorize?state={state}&nonce={nonce}"

    async def exchange_code(
        self,
        code: str,
        *,
        redirect_uri: str,
        code_verifier: str | None = None,
        nonce: str | None = None,
    ) -> dict[str, Any]:
        return {}

    def map_claims(self, userinfo: dict[str, Any]) -> IdpClaims:
        return IdpClaims(
            external_subject=str(userinfo["sub"]),
            email=str(userinfo["email"]),
            first_name=str(userinfo.get("given_name", "")),
            last_name=str(userinfo.get("family_name", "")),
            email_verified=bool(userinfo.get("email_verified", False)),
            issuer=_IDP_ISSUER,
        )


@pytest.fixture()
async def idp_link_env(
    integration_context: Context,
) -> AsyncGenerator[tuple[Context, Identity, list[str]], None]:
    """Configure IdP on integration_context and seed a non-admin ``users:write`` creator."""
    ctx = integration_context
    prior_idp = ctx.config.auth.idp
    ctx.config.auth.idp = IdpConfig(
        enabled=True,
        provider=_IDP_PROVIDER,
        issuer=_IDP_ISSUER,
        client_id="link-test-client",
        client_secret=SecretStr("link-test-secret"),
        scopes=["openid", "email", "profile"],
    )

    tracked_user_ids: list[str] = []
    async with ctx.admin_db.transaction() as session:
        creator = await UserRepository.create(
            session,
            email="creator-users-write@test.local",
            first_name="Creator",
            last_name="User",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        await UserSecretRepository.create(session, user_id=creator.id, created_by="usr_test")
        await UserPermissionGrantRepository.set_permissions(
            session,
            creator.id,
            permissions={"users:read", "users:write"},
            granted_by=None,
            created_by="usr_test",
        )
        tracked_user_ids.append(creator.id)

    creator_identity = Identity(
        sub=creator.id,
        email=creator.email,
        permissions=["users:read", "users:write"],
    )

    yield ctx, creator_identity, tracked_user_ids

    ctx.config.auth.idp = prior_idp
    async with ctx.admin_db.transaction() as session:
        for uid in tracked_user_ids:
            await session.execute(delete(AuditEntry).where(AuditEntry.target_id == uid))
            await session.execute(delete(ExternalIdentity).where(ExternalIdentity.user_id == uid))
            await session.execute(delete(InviteToken).where(InviteToken.user_id == uid))
            await session.execute(
                delete(UserPermissionGrant).where(UserPermissionGrant.user_id == uid)
            )
            await session.execute(delete(UserSecret).where(UserSecret.user_id == uid))
            await session.execute(delete(User).where(User.id == uid))


async def test_link_after_invite_redeemed_clears_password(
    idp_link_env: tuple[Context, Identity, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Linking an external identity to an invite-redeemed user clears the local
    password, revokes active login sessions, marks invite_state=accepted, and
    records an update audit entry."""
    ctx, creator_identity, tracked_user_ids = idp_link_env
    adapter = _FakeIdpAdapter()
    monkeypatch.setattr(AuthorizeService, "_get_idp_adapter", lambda self: adapter)

    created = await UserService(ctx).create(
        UserCreatePayload(
            email="preclaimed-redeemed@test.local",
            first_name="New",
            last_name="Member",
        ),
        identity=creator_identity,
    )
    user_id = created.user.id
    tracked_user_ids.append(user_id)

    redeem_bundle = await InviteService(ctx).redeem(created.invite_token, _INITIAL_PASSWORD)
    assert redeem_bundle.access_token

    claims = adapter.map_claims(
        {
            "sub": "ext-sub-redeemed",
            "email": "preclaimed-redeemed@test.local",
            "given_name": "New",
            "family_name": "Member",
            "email_verified": True,
        }
    )
    resolved_id = await AuthorizeService(ctx)._resolve_or_create_user(claims)
    assert resolved_id == user_id

    auth_svc = AuthService(ctx)
    with pytest.raises(InvalidCredentialsError):
        await auth_svc.login(
            LoginPayload(email="preclaimed-redeemed@test.local", password=_INITIAL_PASSWORD)
        )

    with pytest.raises(InvalidTokenError):
        await auth_svc.verify_token(redeem_bundle.access_token)

    async with ctx.admin_db.session() as session:
        user = await UserRepository.get_by_id(session, user_id)
        secret = await UserSecretRepository.get_by_user_id(session, user_id)
        audits = list(
            (
                await session.execute(
                    select(AuditEntry).where(
                        AuditEntry.target_id == user_id,
                        AuditEntry.target_type == AuditTargetType.USER.value,
                        AuditEntry.action == AuditAction.UPDATE.value,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert user is not None
    assert user.invite_state == InviteState.ACCEPTED
    assert user.session_generation >= 1
    assert secret is not None
    assert secret.password_hash is None
    assert any(
        a.reason == "linked external identity; local password and invites cleared" for a in audits
    )


async def test_link_before_invite_redeemed_cancels_invite(
    idp_link_env: tuple[Context, Identity, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Linking an external identity before the invite token is used cancels the
    outstanding invite token so a subsequent redeem is refused."""
    ctx, creator_identity, tracked_user_ids = idp_link_env
    adapter = _FakeIdpAdapter()
    monkeypatch.setattr(AuthorizeService, "_get_idp_adapter", lambda self: adapter)

    created = await UserService(ctx).create(
        UserCreatePayload(
            email="preclaimed-pending@test.local",
            first_name="Pending",
            last_name="Member",
        ),
        identity=creator_identity,
    )
    user_id = created.user.id
    tracked_user_ids.append(user_id)

    claims = adapter.map_claims(
        {
            "sub": "ext-sub-pending",
            "email": "preclaimed-pending@test.local",
            "given_name": "Pending",
            "family_name": "Member",
            "email_verified": True,
        }
    )
    resolved_id = await AuthorizeService(ctx)._resolve_or_create_user(claims)
    assert resolved_id == user_id

    with pytest.raises(InviteTokenAlreadyRedeemedError):
        await InviteService(ctx).redeem(created.invite_token, _INITIAL_PASSWORD)


async def test_link_pending_account_marks_accepted(
    idp_link_env: tuple[Context, Identity, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Linking an external identity to a pending user transitions invite_state
    to accepted and cancels active invites."""
    ctx, creator_identity, tracked_user_ids = idp_link_env
    adapter = _FakeIdpAdapter()
    monkeypatch.setattr(AuthorizeService, "_get_idp_adapter", lambda self: adapter)

    created = await UserService(ctx).create(
        UserCreatePayload(
            email="pending-to-accepted@test.local",
            first_name="Pending",
            last_name="Accepted",
        ),
        identity=creator_identity,
    )
    user_id = created.user.id
    tracked_user_ids.append(user_id)
    assert created.user.invite_state == InviteState.PENDING

    claims = adapter.map_claims(
        {
            "sub": "ext-sub-accepted",
            "email": "pending-to-accepted@test.local",
            "given_name": "Pending",
            "family_name": "Accepted",
            "email_verified": True,
        }
    )
    resolved_id = await AuthorizeService(ctx)._resolve_or_create_user(claims)
    assert resolved_id == user_id

    async with ctx.admin_db.session() as session:
        user = await UserRepository.get_by_id(session, user_id)
        active_tokens = await InviteTokenRepository.get_active_for_user(session, user_id)
    assert user is not None
    assert user.invite_state == InviteState.ACCEPTED
    assert active_tokens == []
    view = await UserService(ctx).get_by_id(user_id)
    assert view.invite_state == InviteState.ACCEPTED


async def test_unverified_email_still_rejected(
    idp_link_env: tuple[Context, Identity, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unverified IdP email matching an existing account continues to fail closed."""
    ctx, creator_identity, tracked_user_ids = idp_link_env
    adapter = _FakeIdpAdapter()
    monkeypatch.setattr(AuthorizeService, "_get_idp_adapter", lambda self: adapter)

    created = await UserService(ctx).create(
        UserCreatePayload(
            email="unverified-collision@test.local",
            first_name="Existing",
            last_name="User",
        ),
        identity=creator_identity,
    )
    user_id = created.user.id
    tracked_user_ids.append(user_id)

    claims = adapter.map_claims(
        {
            "sub": "ext-sub-unverified",
            "email": "unverified-collision@test.local",
            "given_name": "Existing",
            "family_name": "User",
            "email_verified": False,
        }
    )
    with pytest.raises(InvalidGrantError, match="not verified"):
        await AuthorizeService(ctx)._resolve_or_create_user(claims)
