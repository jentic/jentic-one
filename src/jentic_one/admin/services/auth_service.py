"""Authentication service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog

from jentic_one.admin.core.permissions import ORG_ADMIN
from jentic_one.admin.core.schema.setup_sentinel import SETUP_SENTINEL_ID, SetupSentinel
from jentic_one.admin.repos import (
    UserPermissionGrantRepository,
    UserRepository,
    UserSecretRepository,
)
from jentic_one.admin.services._support.passwords import (
    MIN_PASSWORD_LENGTH,
    PASSWORD_TOO_SHORT_MESSAGE,
    hash_password,
    verify_password,
)
from jentic_one.admin.services._support.tokens import issue_jwt
from jentic_one.admin.services.errors import (
    AccountLockedError,
    InvalidCredentialsError,
    InvalidInputError,
    SetupAlreadyCompleteError,
    UserEmailNotFoundError,
)
from jentic_one.admin.services.metrics import login_counter
from jentic_one.admin.services.permission_service import PermissionService
from jentic_one.admin.services.schemas.auth import (
    ChangePasswordPayload,
    Identity,
    LoginPayload,
    TokenBundle,
)
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit
from jentic_one.shared.auth.verify import verify_token
from jentic_one.shared.context import Context
from jentic_one.shared.db.errors import DatabaseIntegrityError
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.models import ActorType, InviteState

logger = structlog.get_logger(__name__)


class AuthService:
    """Handles login, password management, and token verification."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def login(self, payload: LoginPayload) -> TokenBundle:
        config = self._ctx.config.admin.auth

        account_locked_user_id: str | None = None

        async with self._ctx.admin_db.session() as session:
            user = await UserRepository.get_by_email(session, payload.email)
            if user is None:
                raise InvalidCredentialsError()

            if not user.active:
                raise InvalidCredentialsError()

            secret = await UserSecretRepository.get_by_user_id(session, user.id)
            if secret is None or secret.password_hash is None:
                raise InvalidCredentialsError()

            if secret.locked_until is not None and secret.locked_until > datetime.now(UTC):
                # Defer the audit write until *after* this read session closes:
                # holding this read connection open while opening a write
                # transaction on the same database self-deadlocks under SQLite
                # (the writer waits on a lock the outer read still holds).
                account_locked_user_id = user.id
            else:
                password_valid = verify_password(payload.password, secret.password_hash)
                failed_count = secret.failed_login_count

        if account_locked_user_id is not None:
            login_counter.add(1, {"outcome": "lockout"})
            async with self._ctx.admin_db.transaction() as audit_session:
                await record_audit(
                    audit_session,
                    action=AuditAction.LOGIN_FAILED,
                    target_type=AuditTargetType.SESSION,
                    target_id=account_locked_user_id,
                    actor_type=ActorType.USER,
                    actor_id=account_locked_user_id,
                    reason="account_locked",
                    origin=None,
                )
            raise AccountLockedError(account_locked_user_id)

        if not password_valid:
            async with self._ctx.admin_db.transaction() as session:
                await UserSecretRepository.record_failed_login(session, user.id)
                if failed_count + 1 >= config.failed_login_lockout_threshold:
                    locked_until = datetime.now(UTC) + timedelta(
                        seconds=config.failed_login_lockout_seconds
                    )
                    await UserSecretRepository.lock_until(
                        session, user.id, locked_until=locked_until
                    )
                    logger.warning("account_locked", user_id=user.id, locked_until=locked_until)
                    login_counter.add(1, {"outcome": "lockout"})
                else:
                    logger.warning("login_failed", email=payload.email)
                    login_counter.add(1, {"outcome": "failure"})
                await record_audit(
                    session,
                    action=AuditAction.LOGIN_FAILED,
                    target_type=AuditTargetType.SESSION,
                    target_id=user.id,
                    actor_type=ActorType.USER,
                    actor_id=user.id,
                    origin=None,
                )
            raise InvalidCredentialsError()

        async with self._ctx.admin_db.transaction() as session:
            await UserSecretRepository.reset_failed_logins(session, user.id)
            await record_audit(
                session,
                action=AuditAction.LOGIN,
                target_type=AuditTargetType.SESSION,
                target_id=user.id,
                actor_type=ActorType.USER,
                actor_id=user.id,
                origin=None,
            )

        logger.info("login_success", user_id=user.id)
        login_counter.add(1, {"outcome": "success"})

        perm_service = PermissionService(self._ctx)
        perms_view = await perm_service.get_effective_for_user(user.id)

        claims = {
            "sub": user.id,
            "email": user.email,
            "permissions": perms_view.effective,
            "must_change_password": user.must_change_password,
        }
        token = issue_jwt(claims, config.jwt_secret.get_secret_value(), config.jwt_ttl_seconds)
        return TokenBundle(
            access_token=token,
            expires_in=config.jwt_ttl_seconds,
            must_change_password=user.must_change_password,
        )

    async def bootstrap_admin(
        self,
        *,
        email: str,
        password: str,
        first_name: str = "Admin",
        last_name: str = "User",
    ) -> TokenBundle:
        """Create the first admin user (one-time, first-run setup).

        Self-closing: succeeds only while the platform has not been set up. A
        single-row ``setup_sentinel`` table is the concurrency backstop — its
        fixed primary key (``SETUP_SENTINEL_ID``) is inserted in the same
        transaction as the first user, so two racing callers (even with *different*
        emails, e.g. an agent trying to land-grab the account) collide on the PK
        and only one wins. The empty-users ``count()`` check alone cannot serialize
        this (no range lock under READ COMMITTED), and the unique email index only
        covers same-email races. Grants ``org:admin`` and returns a ready-to-use
        token bundle (auto-login) with ``must_change_password=False`` — the
        operator chose this password.
        """
        if len(password) < MIN_PASSWORD_LENGTH:
            raise InvalidInputError(PASSWORD_TOO_SHORT_MESSAGE)

        config = self._ctx.config.admin

        try:
            async with self._ctx.admin_db.transaction() as session:
                if await UserRepository.count(session) > 0:
                    raise SetupAlreadyCompleteError()

                # Claim the singleton lock first. A racing caller's identical
                # insert trips the primary-key constraint at flush() below, which
                # we map to "already set up" — this is what serializes distinct
                # callers, not the user-email index.
                session.add(SetupSentinel(id=SETUP_SENTINEL_ID))

                # The first admin has no prior actor to attribute creation to, so
                # it is its own provenance: generate the user id up front and use
                # it as created_by. Avoids any "system" sentinel actor.
                admin_id = generate_ksuid("usr")
                user = await UserRepository.create(
                    session,
                    id=admin_id,
                    email=email,
                    first_name=first_name,
                    last_name=last_name,
                    active=True,
                    must_change_password=False,
                    invite_state=InviteState.REDEEMED,
                    created_by=admin_id,
                )
                await session.flush()

                await UserSecretRepository.set_password_hash(
                    session, user.id, password_hash=hash_password(password), created_by=user.id
                )
                await UserPermissionGrantRepository.set_permissions(
                    session,
                    user.id,
                    permissions={ORG_ADMIN},
                    granted_by=user.id,
                    created_by=user.id,
                )
                await record_audit(
                    session,
                    action=AuditAction.CREATE,
                    target_type=AuditTargetType.USER,
                    target_id=user.id,
                    actor_type=ActorType.USER,
                    actor_id=user.id,
                    reason="first-run admin bootstrap",
                    origin=None,
                )
        except DatabaseIntegrityError as exc:
            # The sentinel PK (or the unique email index) tripped — a concurrent
            # caller won the race. transaction() converts the IntegrityError into
            # DatabaseIntegrityError whether the violation surfaces at the in-body
            # flush() or at commit, so catching that one type keeps the contract a
            # clean 410 instead of a 500.
            raise SetupAlreadyCompleteError() from exc

        logger.info("bootstrap_admin_created", user_id=user.id, email=email)

        perm_service = PermissionService(self._ctx)
        perms_view = await perm_service.get_effective_for_user(user.id)

        claims = {
            "sub": user.id,
            "email": user.email,
            "permissions": perms_view.effective,
            "must_change_password": False,
        }
        token = issue_jwt(
            claims, config.auth.jwt_secret.get_secret_value(), config.auth.jwt_ttl_seconds
        )
        return TokenBundle(
            access_token=token,
            expires_in=config.auth.jwt_ttl_seconds,
            must_change_password=False,
        )

    async def change_own_password(
        self, payload: ChangePasswordPayload, *, identity: Identity
    ) -> TokenBundle:
        """Rotate the caller's password and return a fresh token bundle.

        Returning a new token is the single source of truth for clearing the
        ``must_change_password`` gate: the old token still carries the stale
        claim, so without a re-mint the client would loop. The new token reflects
        the cleared gate and current permissions.
        """
        if len(payload.new_password) < MIN_PASSWORD_LENGTH:
            raise InvalidInputError(PASSWORD_TOO_SHORT_MESSAGE)

        user_id = identity.sub
        async with self._ctx.admin_db.transaction() as session:
            secret = await UserSecretRepository.get_by_user_id(session, user_id)
            if secret is None or secret.password_hash is None:
                raise InvalidCredentialsError()

            if not verify_password(payload.current_password, secret.password_hash):
                raise InvalidCredentialsError()

            hashed = hash_password(payload.new_password)
            await UserSecretRepository.set_password_hash(
                session, user_id, password_hash=hashed, created_by=user_id
            )

            user = await UserRepository.get_by_id(session, user_id)
            if user is not None and user.must_change_password:
                await UserRepository.update(session, user_id, must_change_password=False)

            await record_audit(
                session,
                action=AuditAction.UPDATE,
                target_type=AuditTargetType.USER,
                target_id=user_id,
                actor_type=ActorType.USER,
                actor_id=user_id,
                reason="password change",
                origin=identity.origin.value,
            )

        logger.info("password_changed", user_id=user_id)

        config = self._ctx.config.admin
        perm_service = PermissionService(self._ctx)
        perms_view = await perm_service.get_effective_for_user(user_id)
        claims = {
            "sub": user_id,
            "email": user.email if user is not None else identity.email,
            "permissions": perms_view.effective,
            "must_change_password": False,
        }
        token = issue_jwt(
            claims, config.auth.jwt_secret.get_secret_value(), config.auth.jwt_ttl_seconds
        )
        return TokenBundle(
            access_token=token,
            expires_in=config.auth.jwt_ttl_seconds,
            must_change_password=False,
        )

    async def reset_password(self, *, email: str, temporary_password: str) -> str:
        """Operator-initiated password reset: set a temporary, force a rotation.

        For the no-self-service-recovery model: an operator (via ``jenticctl
        reset-password``) sets a one-time credential and flips
        ``must_change_password=True``, so the user logs in once with the temp and
        is forced through the change-password gate, which re-mints a token with
        the gate cleared. The operator never learns the user's standing password —
        this mirrors Keycloak's "Temporary" reset and GitLab/Django admin resets.

        Runs over the same trusted CLI seam as ``bootstrap_admin`` (no request
        identity), so the reset is attributed to the user as its own actor rather
        than a "system" sentinel. Also clears any active lockout, since a user who
        forgot their password may have locked themselves out. Returns the user id.
        """
        if len(temporary_password) < MIN_PASSWORD_LENGTH:
            raise InvalidInputError(PASSWORD_TOO_SHORT_MESSAGE)

        async with self._ctx.admin_db.transaction() as session:
            user = await UserRepository.get_by_email(session, email)
            if user is None:
                raise UserEmailNotFoundError(email)

            await UserSecretRepository.set_password_hash(
                session,
                user.id,
                password_hash=hash_password(temporary_password),
                created_by=user.id,
            )
            await UserSecretRepository.unlock(session, user.id)
            if not user.must_change_password:
                await UserRepository.update(session, user.id, must_change_password=True)

            await record_audit(
                session,
                action=AuditAction.UPDATE,
                target_type=AuditTargetType.USER,
                target_id=user.id,
                actor_type=ActorType.USER,
                actor_id=user.id,
                reason="operator password reset",
                origin=None,
            )

        logger.info("password_reset", user_id=user.id, email=email)
        return user.id

    async def verify_token(self, token: str) -> Identity:
        """Decode and verify a JWT, resolving dynamic permissions."""
        return await verify_token(
            token, secret=self._ctx.config.admin.auth.jwt_secret.get_secret_value(), ctx=self._ctx
        )
