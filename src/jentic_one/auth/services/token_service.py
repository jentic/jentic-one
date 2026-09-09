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
    OAuthClientGrantRepository,
    OAuthClientRepository,
    RefreshTokenRepository,
    ServiceAccountRepository,
    UserRepository,
)
from jentic_one.auth.services.errors import InvalidGrantError
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.ids import generate_ksuid
from jentic_one.shared.models import ActorStatus, ActorType, OAuthClientApprovalStatus
from jentic_one.shared.models.oauth_clients import OAuthGrantStatus

ACCESS_TOKEN_PREFIX = "at_"
REFRESH_TOKEN_PREFIX = "rt_"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _generate_token(prefix: str) -> str:
    return f"{prefix}{secrets.token_urlsafe(32)}"


async def _resolve_client_gate(
    session: AsyncSession, oauth_client_id: str | None
) -> tuple[bool, frozenset[str] | None]:
    """Look up an issuing OAuth client and return (active, scope_ceiling).

    ``active`` is True when the token has no issuing client OR the client is
    active AND approved. The D7 approval gate fails closed here too: the live
    resolvers are the design's safety net, so a pending/denied row — even one
    force-set ``active`` (e.g. by a direct DB edit) — must stop its
    outstanding tokens from resolving. ``scope_ceiling`` is the client's
    ``allowed_scopes`` if set (used to filter the token's scopes at
    introspection time), else None. Kept as a module-level helper so the
    introspect + resolve paths share one shape.
    """
    if oauth_client_id is None:
        return True, None
    client = await OAuthClientRepository.get_by_client_id(session, oauth_client_id)
    if (
        client is None
        or not client.active
        or client.approval_status != OAuthClientApprovalStatus.APPROVED.value
    ):
        return False, None
    ceiling = frozenset(client.allowed_scopes) if client.allowed_scopes is not None else None
    return True, ceiling


def _apply_scope_ceiling(scopes: list[str], ceiling: frozenset[str] | None) -> list[str]:
    if ceiling is None:
        return scopes
    return [s for s in scopes if s in ceiling]


async def resolve_effective_scopes(
    session: AsyncSession,
    *,
    actor_id: str,
    actor_type: str,
    snapshot_scopes: list[str],
    is_ephemeral: bool,
    client_ceiling: frozenset[str] | None,
    grant_ceiling: frozenset[str] | None,
) -> list[str]:
    """The scope set the live resolvers actually enforce for a token.

    Non-ephemeral AGENT/SERVICE_ACCOUNT tokens draw scopes *live* from
    ``actor_scope_grants`` — the mint-time snapshot is dead weight for
    enforcement on these actor types (scope edits take effect immediately by
    design). Ephemeral mints and USER tokens keep their snapshot. Either
    starting set is then intersected with the issuing client's
    ``allowed_scopes`` ceiling and the consent grant's scope set.

    This is the single source of truth shared by the enforcement path
    (:meth:`TokenService.resolve_access_token`) and the RFC 6749 §5.1
    reporting paths (:meth:`TokenService.refresh`,
    ``AuthorizeService.exchange_code``), so the ``scope`` member a token
    response reports can never diverge from what the minted token enforces.
    The broker's ``InProcessTokenResolver`` re-implements the same math over
    raw SQL (it deliberately imports nothing from ``auth``); the
    grant-channel integration suite pins all three against each other.
    """
    scopes = snapshot_scopes
    if not is_ephemeral and actor_type in (ActorType.AGENT, ActorType.SERVICE_ACCOUNT):
        grants = await ActorScopeGrantRepository.list_for_actor(
            session, actor_id, actor_type=actor_type
        )
        scopes = [g.scope for g in grants]
    scopes = _apply_scope_ceiling(scopes, client_ceiling)
    return _apply_scope_ceiling(scopes, grant_ceiling)


async def _resolve_grant_gate(
    session: AsyncSession, oauth_grant_id: str | None
) -> tuple[bool, frozenset[str] | None]:
    """Look up a grant-channel token's consent grant.

    ``active`` is True when the token has no grant OR the grant row exists and
    is ``active`` — a missing or revoked row fails closed, mirroring the D7
    client gate above. ``grant_scopes`` is the consent-time scope set, one leg
    of the quadruple intersection (token scopes ∩ agent live grants ∩ client
    ceiling ∩ grant scopes).
    """
    if oauth_grant_id is None:
        return True, None
    grant = await OAuthClientGrantRepository.get_by_id(session, oauth_grant_id)
    if grant is None or grant.status != OAuthGrantStatus.ACTIVE.value:
        return False, None
    return True, frozenset(grant.scopes)


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
        self,
        actor_id: str,
        actor_type: ActorType,
        scopes: list[str],
        *,
        oauth_client_id: str | None = None,
        oauth_grant_id: str | None = None,
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
                oauth_client_id=oauth_client_id,
                oauth_grant_id=oauth_grant_id,
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
                oauth_client_id=oauth_client_id,
                oauth_grant_id=oauth_grant_id,
            )
            if oauth_grant_id is not None:
                # last_used_at rides on the token write paths (mint here,
                # rotation in refresh): "last time the client obtained tokens"
                # without turning the read-path resolvers into writers.
                await OAuthClientGrantRepository.touch_last_used(session, oauth_grant_id)
            audit_after: dict[str, object] = {"token_type": "pair", "scopes": scopes}
            if oauth_client_id is not None:
                audit_after["oauth_client_id"] = oauth_client_id
            if oauth_grant_id is not None:
                audit_after["oauth_grant_id"] = oauth_grant_id
            await record_audit(
                session,
                action=AuditAction.CREATE,
                target_type=AuditTargetType.TOKEN,
                target_id=family_id,
                actor_type=actor_type,
                actor_id=actor_id,
                after=audit_after,
                origin=None,
            )

        # See issue_access_only: retry a transient admin-DB write-lock so the
        # mint/token path under concurrent writers doesn't 500 on first contention.
        await self._ctx.admin_db.run_in_transaction(_write)

        return access_plain, refresh_plain

    async def refresh(
        self, refresh_token: str, *, client_id: str | None = None
    ) -> tuple[str, str, list[str]]:
        """Rotate a refresh token. Returns (access_token, refresh_token, scopes).

        ``scopes`` is the rotated access token's *effective* set, computed at
        rotation time exactly the way the live resolvers enforce it
        (:func:`resolve_effective_scopes`): live ``actor_scope_grants`` ∩
        client ceiling ∩ grant scopes for non-ephemeral AGENT/SA actors, the
        family snapshot ∩ ceilings for USER actors. The token rows still
        carry the family's mint-time snapshot — enforcement for AGENT/SA
        ignores it, and re-stamping would break USER-token semantics.

        Implements reuse detection: if the refresh token has already been consumed,
        the entire token family is revoked. Uses SELECT FOR UPDATE to prevent TOCTOU
        races on concurrent refresh attempts.

        For confidential-client tokens (``oauth_client_id`` set), ``client_id``
        must match the issuing client (RFC 6749 §6). A mismatch revokes the
        family (potential token theft).
        """
        token_hash = _hash_token(refresh_token)
        reuse_detected = False
        client_mismatch = False
        client_ceiling: frozenset[str] | None = None
        grant_ceiling: frozenset[str] | None = None

        async with self._ctx.admin_db.transaction() as session:
            rt = await RefreshTokenRepository.get_by_hash(session, token_hash, for_update=True)

            if rt is None or rt.revoked_at is not None:
                raise InvalidGrantError("refresh token not found or revoked")

            if rt.expires_at <= datetime.now(UTC):
                raise InvalidGrantError("refresh token expired")

            if rt.oauth_client_id is not None:
                oauth_client = await OAuthClientRepository.get_by_client_id(
                    session, rt.oauth_client_id
                )
                if (
                    oauth_client is None
                    or not oauth_client.active
                    or oauth_client.approval_status != OAuthClientApprovalStatus.APPROVED.value
                ):
                    # The D7 approval gate fails closed here too: deny flips
                    # active off, but a pending row force-set active must
                    # still never mint tokens.
                    raise InvalidGrantError("issuing OAuth client has been deactivated")
                if oauth_client.allowed_scopes is not None:
                    client_ceiling = frozenset(oauth_client.allowed_scopes)
                if client_id is None:
                    raise InvalidGrantError("client authentication required")
                if client_id != rt.oauth_client_id:
                    await RefreshTokenRepository.revoke_family(session, rt.token_family_id)
                    await AccessTokenRepository.revoke_family(session, rt.token_family_id)
                    client_mismatch = True
                    await record_audit(
                        session,
                        action=AuditAction.REVOKE,
                        target_type=AuditTargetType.TOKEN,
                        target_id=rt.token_family_id,
                        actor_type=rt.actor_type,
                        actor_id=rt.actor_id,
                        reason="refresh with wrong client_id",
                        origin=None,
                    )

            if not client_mismatch:
                if rt.consumed_at is not None:
                    # Reuse detection runs BEFORE the grant re-check: a
                    # replayed, already-consumed token must always trigger the
                    # family sweep and the "reuse detected" audit row — that
                    # telemetry must not be lost just because the consent
                    # grant was revoked in the meantime. The grant
                    # gate below still fails rotation closed either way.
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
                    if rt.oauth_grant_id is not None:
                        # Grant re-check on every rotation: a
                        # revoked consent grant must fail refresh closed even
                        # if the family's token rows were somehow missed by
                        # the revoke sweep.
                        grant = await OAuthClientGrantRepository.get_by_id(
                            session, rt.oauth_grant_id
                        )
                        if grant is None or grant.status != OAuthGrantStatus.ACTIVE.value:
                            raise InvalidGrantError("consent grant has been revoked")
                        grant_ceiling = frozenset(grant.scopes)

                    if not await _actor_is_active(session, rt.actor_id, rt.actor_type):
                        raise InvalidGrantError("actor is not active")

                    access_plain = _generate_token(ACCESS_TOKEN_PREFIX)
                    refresh_plain = _generate_token(REFRESH_TOKEN_PREFIX)
                    snapshot = list(rt.scopes)
                    # Reported set (RFC 6749 §5.1) — computed the way the
                    # resolvers will enforce it for the new access token, not
                    # the family snapshot: for AGENT/SA actors the resolvers
                    # ignore the snapshot and re-derive live on every request,
                    # so reporting rt.scopes would over-report scopes revoked
                    # since the original mint (and under-report ones granted
                    # since) — the exact #1260 failure mode.
                    scopes = await resolve_effective_scopes(
                        session,
                        actor_id=rt.actor_id,
                        actor_type=rt.actor_type,
                        snapshot_scopes=snapshot,
                        is_ephemeral=False,
                        client_ceiling=client_ceiling,
                        grant_ceiling=grant_ceiling,
                    )
                    now = datetime.now(UTC)

                    await AccessTokenRepository.create(
                        session,
                        token_hash=_hash_token(access_plain),
                        actor_id=rt.actor_id,
                        actor_type=rt.actor_type,
                        scopes=snapshot,
                        token_family_id=rt.token_family_id,
                        expires_at=now + timedelta(seconds=self.access_ttl_seconds),
                        created_by=rt.actor_id,
                        oauth_client_id=rt.oauth_client_id,
                        oauth_grant_id=rt.oauth_grant_id,
                    )
                    new_refresh = await RefreshTokenRepository.create(
                        session,
                        token_hash=_hash_token(refresh_plain),
                        actor_id=rt.actor_id,
                        actor_type=rt.actor_type,
                        scopes=snapshot,
                        token_family_id=rt.token_family_id,
                        expires_at=now + timedelta(seconds=self._refresh_ttl),
                        created_by=rt.actor_id,
                        oauth_client_id=rt.oauth_client_id,
                        oauth_grant_id=rt.oauth_grant_id,
                    )
                    if rt.oauth_grant_id is not None:
                        # See issue_pair: last_used_at is maintained on the
                        # token write paths, not per resolved request.
                        await OAuthClientGrantRepository.touch_last_used(session, rt.oauth_grant_id)

                    await RefreshTokenRepository.consume(
                        session, rt.id, replaced_by_id=new_refresh.id
                    )
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
        if client_mismatch:
            raise InvalidGrantError("client_id mismatch")

        return access_plain, refresh_plain, scopes

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
        the actor-status check, the issuing-client active check, and the
        client's scope ceiling — so an operator inspecting a token after a
        client is deactivated or its scopes are tightened sees the truth, not
        just the token row's own state.
        """
        token_hash = _hash_token(token)
        now = datetime.now(UTC)

        async with self._ctx.admin_db.session() as session:
            if token.startswith(ACCESS_TOKEN_PREFIX):
                at = await AccessTokenRepository.get_by_hash(session, token_hash)
                if at is None:
                    return {"active": False}
                client_active, ceiling = await _resolve_client_gate(session, at.oauth_client_id)
                grant_active, grant_scopes = await _resolve_grant_gate(session, at.oauth_grant_id)
                active = (
                    at.revoked_at is None
                    and at.expires_at > now
                    and await _actor_is_active(session, at.actor_id, at.actor_type)
                    and client_active
                    and grant_active
                )
                scopes = _apply_scope_ceiling(list(at.scopes), ceiling)
                scopes = _apply_scope_ceiling(scopes, grant_scopes)
                return {
                    "active": active,
                    "sub": at.actor_id,
                    "scope": " ".join(scopes),
                    "exp": int(at.expires_at.timestamp()),
                    "token_type": "access_token",
                }
            elif token.startswith(REFRESH_TOKEN_PREFIX):
                rt = await RefreshTokenRepository.get_by_hash(session, token_hash)
                if rt is None:
                    return {"active": False}
                client_active, ceiling = await _resolve_client_gate(session, rt.oauth_client_id)
                grant_active, grant_scopes = await _resolve_grant_gate(session, rt.oauth_grant_id)
                active = (
                    rt.revoked_at is None
                    and rt.consumed_at is None
                    and rt.expires_at > now
                    and await _actor_is_active(session, rt.actor_id, rt.actor_type)
                    and client_active
                    and grant_active
                )
                scopes = _apply_scope_ceiling(list(rt.scopes), ceiling)
                scopes = _apply_scope_ceiling(scopes, grant_scopes)
                return {
                    "active": active,
                    "sub": rt.actor_id,
                    "scope": " ".join(scopes),
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

            client_scope_ceiling: frozenset[str] | None = None
            if at.oauth_client_id is not None:
                oauth_client = await OAuthClientRepository.get_by_client_id(
                    session, at.oauth_client_id
                )
                if (
                    oauth_client is None
                    or not oauth_client.active
                    or oauth_client.approval_status != OAuthClientApprovalStatus.APPROVED.value
                ):
                    # The D7 approval gate fails closed at the live resolver —
                    # a denied/pending row force-set active must not keep its
                    # outstanding tokens resolving (mirrors the refresh gate).
                    return None
                if oauth_client.allowed_scopes is not None:
                    client_scope_ceiling = frozenset(oauth_client.allowed_scopes)

            grant_scope_ceiling: frozenset[str] | None = None
            if at.oauth_grant_id is not None:
                # The grant gate: a grant-channel token
                # whose consent grant is missing or revoked must stop
                # resolving immediately — the per-grant kill switch's live
                # half (the revoke sweep is the other). Fail closed like the
                # client gate above.
                grant_active, grant_scope_ceiling = await _resolve_grant_gate(
                    session, at.oauth_grant_id
                )
                if not grant_active:
                    return None

            parent_actor_id: str | None = None
            if at.actor_type == ActorType.AGENT:
                # One lookup serves both parent resolution and the status check.
                agent = await AgentRepository.get_by_id(session, at.actor_id)
                parent_actor_id = agent.owner_id if agent is not None else None
                actor_active = agent is not None and agent.status == ActorStatus.ACTIVE
            else:
                actor_active = await _actor_is_active(session, at.actor_id, at.actor_type)

            # Live grants (non-ephemeral AGENT/SA) or snapshot (ephemeral,
            # USER), intersected with the client ceiling and the grant's
            # scope set (the quadruple intersection) — via the same helper
            # the §5.1 reporting paths use, so reported == enforced.
            scopes = await resolve_effective_scopes(
                session,
                actor_id=at.actor_id,
                actor_type=at.actor_type,
                snapshot_scopes=list(at.scopes),
                is_ephemeral=at.is_ephemeral,
                client_ceiling=client_scope_ceiling,
                grant_ceiling=grant_scope_ceiling,
            )

        active = at.revoked_at is None and at.expires_at > now and actor_active
        return Identity(
            sub=at.actor_id,
            actor_type=ActorType(at.actor_type),
            permissions=scopes,
            expires_at=at.expires_at,
            active=active,
            parent_actor_id=parent_actor_id,
            oauth_client_id=at.oauth_client_id,
            oauth_grant_id=at.oauth_grant_id,
        )
