"""Invite token service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from jentic_one.admin.repos import (
    InviteTokenRepository,
    UserRepository,
    UserSecretRepository,
)
from jentic_one.admin.services._support.passwords import hash_password
from jentic_one.admin.services._support.tokens import (
    generate_invite_token,
    hash_invite_token,
    issue_jwt,
)
from jentic_one.admin.services.errors import (
    InvalidInputError,
    InviteTokenAlreadyRedeemedError,
    InviteTokenExpiredError,
    InviteTokenNotFoundError,
    UserNotFoundError,
)
from jentic_one.admin.services.permission_service import PermissionService
from jentic_one.admin.services.schemas.auth import TokenBundle
from jentic_one.admin.services.schemas.invites import InviteIssued
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType, InviteState

logger = structlog.get_logger(__name__)


class InviteService:
    """Manages invite token lifecycle: issue, reissue, and redeem."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def issue_for_user(
        self,
        session: Any,
        user_id: str,
        ttl_days: int | None = None,
        *,
        actor_id: str,
    ) -> InviteIssued:
        """Issue a new invite token within an existing session (no commit)."""
        config = self._ctx.config.admin.invite
        days = ttl_days if ttl_days is not None else config.ttl_days

        plaintext = generate_invite_token()
        token_hash = hash_invite_token(plaintext, config.pepper.get_secret_value())
        expires_at = datetime.now(UTC) + timedelta(days=days)

        await InviteTokenRepository.create(
            session,
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            created_by=actor_id,
        )
        await record_audit(
            session,
            action=AuditAction.CREATE,
            target_type=AuditTargetType.INVITE_TOKEN,
            target_id=user_id,
            actor_type=ActorType.USER,
            actor_id=actor_id,
            origin=None,
        )
        return InviteIssued(token=plaintext, expires_at=expires_at)

    async def reissue(self, user_id: str, *, identity: Identity) -> InviteIssued:
        """Invalidate active tokens and issue a new one."""
        config = self._ctx.config.admin.invite

        async with self._ctx.admin_db.transaction() as session:
            active_tokens = await InviteTokenRepository.get_active_for_user(session, user_id)
            for token in active_tokens:
                await InviteTokenRepository.redeem(session, token.id)

            plaintext = generate_invite_token()
            token_hash = hash_invite_token(plaintext, config.pepper.get_secret_value())
            expires_at = datetime.now(UTC) + timedelta(days=config.ttl_days)

            await InviteTokenRepository.create(
                session,
                user_id=user_id,
                token_hash=token_hash,
                expires_at=expires_at,
                created_by=identity.sub,
            )

            await record_audit(
                session,
                action=AuditAction.CREATE,
                target_type=AuditTargetType.INVITE_TOKEN,
                target_id=user_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                origin=identity.origin.value,
            )

        return InviteIssued(token=plaintext, expires_at=expires_at)

    async def redeem(self, plaintext: str, new_password: str) -> TokenBundle:
        """Redeem an invite token, set password, and return a JWT."""
        if len(new_password) < 12:
            raise InvalidInputError("Password must be at least 12 characters")

        config = self._ctx.config.admin
        token_hash = hash_invite_token(plaintext, config.invite.pepper.get_secret_value())

        async with self._ctx.admin_db.transaction() as session:
            token = await InviteTokenRepository.get_by_token_hash(session, token_hash)
            if token is None:
                raise InviteTokenNotFoundError("unknown")

            if token.redeemed_at is not None:
                raise InviteTokenAlreadyRedeemedError(token.id)

            if token.expires_at < datetime.now(UTC):
                raise InviteTokenExpiredError(token.id)

            await InviteTokenRepository.redeem(session, token.id)

            password_hash = hash_password(new_password)
            await UserSecretRepository.set_password_hash(
                session, token.user_id, password_hash=password_hash, created_by=token.user_id
            )

            user = await UserRepository.get_by_id(session, token.user_id)
            if user is None:
                raise UserNotFoundError(token.user_id)

            await UserRepository.update(
                session,
                token.user_id,
                invite_state=InviteState.REDEEMED,
                must_change_password=False,
            )

            await record_audit(
                session,
                action=AuditAction.UPDATE,
                target_type=AuditTargetType.INVITE_TOKEN,
                target_id=token.id,
                actor_type=ActorType.USER,
                actor_id=token.user_id,
                origin=None,
            )
            await record_audit(
                session,
                action=AuditAction.ENABLE,
                target_type=AuditTargetType.USER,
                target_id=token.user_id,
                actor_type=ActorType.USER,
                actor_id=token.user_id,
                origin=None,
            )

        logger.info("invite_redeemed", user_id=token.user_id)

        perm_service = PermissionService(self._ctx)
        perms_view = await perm_service.get_effective_for_user(token.user_id)

        claims = {
            "sub": token.user_id,
            "email": user.email,
            # Explicit self-description so refresh can fail closed (see
            # AuthService.refresh) instead of assuming a default actor type.
            "actor_type": ActorType.USER.value,
            "permissions": perms_view.effective,
            "must_change_password": False,
            # Redeeming an invite sets the password — a fresh credential proof,
            # so the absolute session window (see AuthService.refresh) starts now.
            "auth_time": int(datetime.now(UTC).timestamp()),
        }
        jwt_token = issue_jwt(
            claims, config.auth.jwt_secret.get_secret_value(), config.auth.jwt_ttl_seconds
        )
        return TokenBundle(
            access_token=jwt_token,
            expires_in=config.auth.jwt_ttl_seconds,
            must_change_password=False,
        )
