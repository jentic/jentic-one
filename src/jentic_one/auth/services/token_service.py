"""Token issuance, refresh, revocation, and introspection service."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.repos import (
    AccessTokenRepository,
    ActorScopeGrantRepository,
    AgentRepository,
    RefreshTokenRepository,
    ServiceAccountRepository,
    UserRepository,
)
from jentic_one.auth.services.errors import InvalidGrantError
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.models import ActorStatus, ActorType

ACCESS_TOKEN_PREFIX = "at_"
REFRESH_TOKEN_PREFIX = "rt_"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _generate_token(prefix: str) -> str:
    return f"{prefix}{secrets.token_urlsafe(32)}"


async def _actor_is_active(session: AsyncSession, actor_id: str, actor_type: str) -> bool:
    """Whether the token's actor is currently allowed to authenticate.

    Disabling an actor must kill its *outstanding* tokens, not just block new
    mints (#1136) — so every token verdict re-checks the actor row. Fails
    closed when the actor row is missing (e.g. a hard-deleted user whose
    tokens were never revoked). Unknown actor types are left to their token's
    own revocation/expiry checks.
    """
    if actor_type == ActorType.AGENT:
        agent = await AgentRepository.get_by_id(session, actor_id)
        return agent is not None and agent.status == ActorStatus.ACTIVE
    if actor_type == ActorType.SERVICE_ACCOUNT:
        sa = await ServiceAccountRepository.get_by_id(session, actor_id)
        return sa is not None and sa.status == ActorStatus.ACTIVE
    if actor_type == ActorType.USER:
        user = await UserRepository.get_by_id(session, actor_id)
        return user is not None and user.active
    return True


class TokenService:
    """Manages opaque token lifecycle: issuance, refresh rotation, revocation, introspection."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    @property
    def access_ttl_seconds(self) -> int:
        return self._ctx.config.auth.access_ttl_seconds

    @property
    def _refresh_ttl(self) -> int:
        return self._ctx.config.auth.refresh_ttl_seconds

    async def issue_access_only(
        self,
        actor_id: str,
        actor_type: ActorType,
        scopes: list[str],
        *,
        ttl_seconds: int | None = None,
    ) -> str:
        """Issue a standalone access token with no refresh token (for ephemeral mints)."""
        access_plain = _generate_token(ACCESS_TOKEN_PREFIX)
        access_hash = _hash_token(access_plain)
        family_id = generate_ksuid("tfam")
        now = datetime.now(UTC)
        ttl = ttl_seconds if ttl_seconds is not None else self.access_ttl_seconds

        async def _write(session: AsyncSession) -> None:
            await AccessTokenRepository.create(
                session,
                token_hash=access_hash,
                actor_id=actor_id,
                actor_type=actor_type,
                scopes=scopes,
                token_family_id=family_id,
                expires_at=now + timedelta(seconds=ttl),
                created_by=actor_id,
                is_ephemeral=True,
            )
            await record_audit(
                session,
                action=AuditAction.CREATE,
                target_type=AuditTargetType.TOKEN,
                target_id=family_id,
                actor_type=actor_type,
                actor_id=actor_id,
                after={"token_type": "access", "scopes": scopes},
                origin=None,
            )

        # Route through run_in_transaction so a transient SQLite write-lock under
        # concurrent mint traffic is retried (with WAL + busy_timeout) rather than
        # surfaced as a 500 on the first attempt. Token generation stays outside
        # so the returned secret is stable across a retry.
        await self._ctx.admin_db.run_in_transaction(_write)

        return access_plain

    async def issue_pair(
        self, actor_id: str, actor_type: ActorType, scopes: list[str]
    ) -> tuple[str, str]:
        """Issue a new access + refresh token pair. Returns (access_token, refresh_token)."""
        access_plain = _generate_token(ACCESS_TOKEN_PREFIX)
        refresh_plain = _generate_token(REFRESH_TOKEN_PREFIX)
        access_hash = _hash_token(access_plain)
        refresh_hash = _hash_token(refresh_plain)
        family_id = generate_ksuid("tfam")
        now = datetime.now(UTC)

        async def _write(session: AsyncSession) -> None:
            await AccessTokenRepository.create(
                session,
                token_hash=access_hash,
                actor_id=actor_id,
                actor_type=actor_type,
                scopes=scopes,
                token_family_id=family_id,
                expires_at=now + timedelta(seconds=self.access_ttl_seconds),
                created_by=actor_id,
                is_ephemeral=False,
            )
            await RefreshTokenRepository.create(
                session,
                token_hash=refresh_hash,
                actor_id=actor_id,
                actor_type=actor_type,
                scopes=scopes,
                token_family_id=family_id,
                expires_at=now + timedelta(seconds=self._refresh_ttl),
                created_by=actor_id,
            )
            await record_audit(
                session,
                action=AuditAction.CREATE,
                target_type=AuditTargetType.TOKEN,
                target_id=family_id,
                actor_type=actor_type,
                actor_id=actor_id,
                after={"token_type": "pair", "scopes": scopes},
                origin=None,
            )

        # See issue_access_only: retry a transient admin-DB write-lock so the
        # mint/token path under concurrent writers doesn't 500 on first contention.
        await self._ctx.admin_db.run_in_transaction(_write)

        return access_plain, refresh_plain

    async def refresh(self, refresh_token: str) -> tuple[str, str]:
        """Rotate a refresh token. Returns a new (access_token, refresh_token) pair.

        Implements reuse detection: if the refresh token has already been consumed,
        the entire token family is revoked. Uses SELECT FOR UPDATE to prevent TOCTOU
        races on concurrent refresh attempts.
        """
        token_hash = _hash_token(refresh_token)
        reuse_detected = False

        async with self._ctx.admin_db.transaction() as session:
            rt = await RefreshTokenRepository.get_by_hash(session, token_hash, for_update=True)

            if rt is None or rt.revoked_at is not None:
                raise InvalidGrantError("refresh token not found or revoked")

            if rt.expires_at <= datetime.now(UTC):
                raise InvalidGrantError("refresh token expired")

            if rt.consumed_at is not None:
                await RefreshTokenRepository.revoke_family(session, rt.token_family_id)
                await AccessTokenRepository.revoke_family(session, rt.token_family_id)
                reuse_detected = True
                await record_audit(
                    session,
                    action=AuditAction.REVOKE,
                    target_type=AuditTargetType.TOKEN,
                    target_id=rt.token_family_id,
                    actor_type=rt.actor_type,
                    actor_id=rt.actor_id,
                    reason="refresh token reuse detected",
                    origin=None,
                )
            else:
                # A non-active actor must not rotate its way to fresh access
                # tokens — refresh is a mint path, so it gets a status gate
                # too (#1136). Unlike the jwt-bearer exchange, PENDING gets no
                # distinct detail here: a refresh token only exists after a
                # successful exchange, so pending-approval polling never
                # reaches this path.
                if not await _actor_is_active(session, rt.actor_id, rt.actor_type):
                    raise InvalidGrantError("actor is not active")

                access_plain = _generate_token(ACCESS_TOKEN_PREFIX)
                refresh_plain = _generate_token(REFRESH_TOKEN_PREFIX)
                now = datetime.now(UTC)

                await AccessTokenRepository.create(
                    session,
                    token_hash=_hash_token(access_plain),
                    actor_id=rt.actor_id,
                    actor_type=rt.actor_type,
                    scopes=list(rt.scopes),
                    token_family_id=rt.token_family_id,
                    expires_at=now + timedelta(seconds=self.access_ttl_seconds),
                    created_by=rt.actor_id,
                )
                new_refresh = await RefreshTokenRepository.create(
                    session,
                    token_hash=_hash_token(refresh_plain),
                    actor_id=rt.actor_id,
                    actor_type=rt.actor_type,
                    scopes=list(rt.scopes),
                    token_family_id=rt.token_family_id,
                    expires_at=now + timedelta(seconds=self._refresh_ttl),
                    created_by=rt.actor_id,
                )

                await RefreshTokenRepository.consume(session, rt.id, replaced_by_id=new_refresh.id)
                await record_audit(
                    session,
                    action=AuditAction.REFRESH,
                    target_type=AuditTargetType.TOKEN,
                    target_id=rt.token_family_id,
                    actor_type=rt.actor_type,
                    actor_id=rt.actor_id,
                    origin=None,
                )

        if reuse_detected:
            raise InvalidGrantError("refresh token reuse detected")

        return access_plain, refresh_plain

    async def revoke(self, token: str, *, identity: Identity) -> None:
        """Revoke a token. No-op if not found or not owned by actor (RFC 7009)."""
        token_hash = _hash_token(token)
        actor_id = identity.sub

        async with self._ctx.admin_db.transaction() as session:
            if token.startswith(ACCESS_TOKEN_PREFIX):
                at = await AccessTokenRepository.get_by_hash(session, token_hash)
                if at is None or at.revoked_at is not None:
                    return
                if actor_id is not None and at.actor_id != actor_id:
                    return
                await AccessTokenRepository.revoke(session, at.id)
                await record_audit(
                    session,
                    action=AuditAction.REVOKE,
                    target_type=AuditTargetType.TOKEN,
                    target_id=at.token_family_id,
                    actor_type=at.actor_type,
                    actor_id=actor_id or at.actor_id,
                    origin=identity.origin.value,
                )
            elif token.startswith(REFRESH_TOKEN_PREFIX):
                rt = await RefreshTokenRepository.get_by_hash(session, token_hash)
                if rt is None or rt.revoked_at is not None:
                    return
                if actor_id is not None and rt.actor_id != actor_id:
                    return
                await RefreshTokenRepository.revoke_family(session, rt.token_family_id)
                await AccessTokenRepository.revoke_family(session, rt.token_family_id)
                await record_audit(
                    session,
                    action=AuditAction.REVOKE,
                    target_type=AuditTargetType.TOKEN,
                    target_id=rt.token_family_id,
                    actor_type=rt.actor_type,
                    actor_id=actor_id or rt.actor_id,
                    origin=identity.origin.value,
                )

    async def introspect(self, token: str) -> dict[str, bool | str | int | None]:
        """Introspect a token per RFC 7662.

        ``active`` reflects the same verdict the resolvers enforce — including
        the actor-status check — so an operator inspecting a token after a
        disable sees the truth, not just the token row's own state.
        """
        token_hash = _hash_token(token)
        now = datetime.now(UTC)

        async with self._ctx.admin_db.session() as session:
            if token.startswith(ACCESS_TOKEN_PREFIX):
                at = await AccessTokenRepository.get_by_hash(session, token_hash)
                if at is None:
                    return {"active": False}
                active = (
                    at.revoked_at is None
                    and at.expires_at > now
                    and await _actor_is_active(session, at.actor_id, at.actor_type)
                )
                return {
                    "active": active,
                    "sub": at.actor_id,
                    "scope": " ".join(at.scopes),
                    "exp": int(at.expires_at.timestamp()),
                    "token_type": "access_token",
                }
            elif token.startswith(REFRESH_TOKEN_PREFIX):
                rt = await RefreshTokenRepository.get_by_hash(session, token_hash)
                if rt is None:
                    return {"active": False}
                active = (
                    rt.revoked_at is None
                    and rt.consumed_at is None
                    and rt.expires_at > now
                    and await _actor_is_active(session, rt.actor_id, rt.actor_type)
                )
                return {
                    "active": active,
                    "sub": rt.actor_id,
                    "scope": " ".join(rt.scopes),
                    "exp": int(rt.expires_at.timestamp()),
                    "token_type": "refresh_token",
                }

        return {"active": False}

    async def resolve_access_token(self, token: str) -> Identity | None:
        """Resolve an opaque access token for downstream middleware.

        For long-lived agent and service-account tokens (an access+refresh pair,
        ``is_ephemeral=False``), scopes are resolved *live* from the actor's
        current ``ActorScopeGrant`` rows rather than the frozen snapshot stored
        on the token. This makes scope edits (grant/revoke, replace, approved
        ``scope:grant`` access requests) take effect immediately without forcing
        a re-mint — the token row's ``scopes`` column is only a mint-time
        snapshot.

        Ephemeral minted tokens (``mint_task_token`` → ``issue_access_only``,
        ``is_ephemeral=True``) keep their frozen snapshot: their scopes are a
        deliberate downscoped subset of the host's grants and must not be
        re-broadened. User tokens also keep their snapshot (their permissions do
        not come from ``ActorScopeGrant``).

        The verdict also re-checks the actor's own status (#1136): a disabled
        or archived actor's outstanding tokens resolve as inactive immediately,
        so disable alone is a working kill switch — no separate token-family
        revocation required.
        """
        if not token.startswith(ACCESS_TOKEN_PREFIX):
            return None

        token_hash = _hash_token(token)
        now = datetime.now(UTC)

        async with self._ctx.admin_db.session() as session:
            at = await AccessTokenRepository.get_by_hash(session, token_hash)

            if at is None:
                return None

            scopes = list(at.scopes)
            parent_actor_id: str | None = None
            if at.actor_type == ActorType.AGENT:
                # One lookup serves both parent resolution and the status check.
                agent = await AgentRepository.get_by_id(session, at.actor_id)
                parent_actor_id = agent.owner_id if agent is not None else None
                actor_active = agent is not None and agent.status == ActorStatus.ACTIVE
            else:
                actor_active = await _actor_is_active(session, at.actor_id, at.actor_type)

            if not at.is_ephemeral and at.actor_type in (
                ActorType.AGENT,
                ActorType.SERVICE_ACCOUNT,
            ):
                grants = await ActorScopeGrantRepository.list_for_actor(
                    session, at.actor_id, actor_type=at.actor_type
                )
                scopes = [g.scope for g in grants]

        active = at.revoked_at is None and at.expires_at > now and actor_active
        return Identity(
            sub=at.actor_id,
            actor_type=ActorType(at.actor_type),
            permissions=scopes,
            expires_at=at.expires_at,
            active=active,
            parent_actor_id=parent_actor_id,
        )
