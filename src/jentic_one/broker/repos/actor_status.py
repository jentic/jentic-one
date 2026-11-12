"""Actor liveness and permission lookup for work that runs after the request is gone.

The sync execute path learns whether its caller is still active — and still
holds the execute permission — from credential resolution (``InProcessTokenResolver``
/ ``ApiKeyResolver`` re-read the actor row and, for agents, the live
``actor_permission_grants`` on every resolve). A queued execution
has no credential to resolve when the worker picks it up, so this answers the
same questions directly from the actor rows, in **one** round-trip per check:

- ``agent`` → active when ``agents.status = 'active'`` (suspended/archived/
  missing → inactive) and, for an owned agent, the owner's ``users.active``
  (an owned agent follows its owner's account status; ownerless agents depend
  on their own status); holds a permission when the exact
  ``actor_permission_grants`` row exists (the grant set an agent's
  credentials resolve their permissions from);
- ``user`` → active when ``users.active`` and ``users.deleted_at IS NULL``
  (disabled/deleted/missing → inactive) — the liveness alone is read through
  the shared ``read_user_status`` the login-JWT verifier uses; holds a
  permission when the implication-expanded (``compute_effective``) set of its
  live ``user_permission_grants`` contains it — the same effective set a
  user's login token is minted with, so ``org:admin`` implies the execute
  permission. Read live, a revocation after enqueue applies to the queued job
  even while the user's token would still carry the permission until it
  expires;
- anything else → neither. That covers the retired ``toolkit`` and
  ``service_account`` identities (theme 8 Phase 4 dropped the
  service-account tables; a job queued under one can never run).

The verdict is terminal for the work being checked. A queued job or approved
hold that comes up while its agent's owner is disabled is refused, not
deferred: re-enabling the owner restores new requests, and refused work must
be submitted again.

The broker's self-contained JWT path asks the liveness question alone
(:meth:`ActorStatusResolver.is_active`, behind a short-TTL cache) once a signed
agent token's claims check out.

Raw SQL against the admin schema — the broker may not import the ``admin`` ORM.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import Boolean, Integer, String, text
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.shared.auth.actor_status import read_user_status
from jentic_one.shared.auth.owner_status import log_agent_owner_inactive
from jentic_one.shared.auth.permission_catalog import compute_effective
from jentic_one.shared.auth.verify import scopes_to_permissions
from jentic_one.shared.db import DatabaseSession
from jentic_one.shared.jobs.protocols import ActorAuthority
from jentic_one.shared.models import ActorType

#: ``status`` of an active, owned agent whose owner is not active.
_OWNER_INACTIVE = "owner_inactive"
# The same owner clause as the broker's ``InProcessTokenResolver``: a queued
# job or approved hold of an owned agent must not run while the owner is
# disabled or deleted. One PK probe on agents, one on users.
_AGENT_STATUS_SQL = (
    "CASE"
    " WHEN a.status <> 'active' THEN a.status"
    " WHEN a.owner_id IS NULL THEN 'active'"
    " WHEN EXISTS ("
    "SELECT 1 FROM users o WHERE o.id = a.owner_id AND o.active AND o.deleted_at IS NULL"
    ") THEN 'active'"
    f" ELSE '{_OWNER_INACTIVE}' END"
)
_AGENT_STATUS = text(
    f"SELECT {_AGENT_STATUS_SQL} AS status, a.owner_id FROM agents a WHERE a.id = :actor_id"
)
_AGENT_AUTHORITY = text(
    f"SELECT {_AGENT_STATUS_SQL} AS status, a.owner_id,"
    " EXISTS (SELECT 1 FROM actor_permission_grants g"
    "  WHERE g.actor_id = a.id AND g.actor_type = 'agent' AND g.permission = :permission)"
    " AS granted"
    " FROM agents a WHERE a.id = :actor_id"
).columns(status=String, owner_id=String, granted=Boolean)
# Liveness matches ``read_user_status(...).accepts_credentials``: a deleted row
# is refused even if it reads active.
_USER_AUTHORITY = text(
    "SELECT (u.active AND u.deleted_at IS NULL) AS active, u.session_generation,"
    " g.permission FROM users u"
    " LEFT JOIN user_permission_grants g ON g.user_id = u.id"
    " WHERE u.id = :actor_id"
).columns(active=Boolean, session_generation=Integer, permission=String)

_ACCESS_TOKEN_LINEAGE = text(
    "SELECT t.actor_id, t.actor_type, t.scopes, t.is_ephemeral,"
    " t.expires_at, t.revoked_at, t.oauth_client_id, t.oauth_grant_id"
    " FROM access_tokens t"
    " WHERE t.token_hash = :token_hash"
).columns(is_ephemeral=Boolean)

_AGENT_API_KEY_LINEAGE = text(
    "SELECT 1 FROM agent_credentials ac"
    " WHERE ac.agent_id = :actor_id AND ac.api_key_hash = :token_hash"
)

_OAUTH_CLIENT_LINEAGE = text(
    "SELECT c.active, (c.approval_status = 'approved') AS approved, c.allowed_scopes"
    " FROM oauth_clients c"
    " WHERE c.client_id = :client_id"
).columns(active=Boolean, approved=Boolean)

_OAUTH_GRANT_LINEAGE = text(
    "SELECT (g.status = 'active' AND g.revoked_at IS NULL) AS active, g.agent_id, g.scopes"
    " FROM oauth_client_grants g"
    " WHERE g.id = :grant_id"
).columns(active=Boolean)

_NONE = ActorAuthority(active=False, holds_permission=False)


def _as_aware_datetime(value: datetime | str) -> datetime:
    """Coerce a DB datetime value to a timezone-aware UTC ``datetime``."""
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _as_scope_list(value: object) -> list[str]:
    """Coerce a JSON scopes column to ``list[str]`` (SQLite may return a JSON string)."""
    if isinstance(value, list):
        return [str(s) for s in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return [str(s) for s in parsed] if isinstance(parsed, list) else []
    return []


class ActorStatusResolver:
    """Reports whether an actor may currently act (fails closed).

    Implements the shared ``ActorStatusReader`` protocol.
    """

    def __init__(self, admin_db: DatabaseSession) -> None:
        self._admin_db = admin_db

    async def is_active(self, *, actor_id: str, actor_type: str) -> bool:
        """``True`` only when the actor row exists and is active."""
        if actor_type == ActorType.AGENT.value:
            async with self._admin_db.read_session() as session:
                row = (await session.execute(_AGENT_STATUS, {"actor_id": actor_id})).one_or_none()
            if row is None:
                return False
            if row.status == _OWNER_INACTIVE:
                log_agent_owner_inactive(actor_id, row.owner_id)
            return bool(row.status == "active")
        if actor_type == ActorType.USER.value:
            status = await read_user_status(self._admin_db, actor_id)
            return status is not None and status.accepts_credentials
        return False

    async def authority(
        self,
        *,
        actor_id: str,
        actor_type: str,
        permission: str,
        auth_token_sha256: str | None = None,
        auth_method: str | None = None,
        oauth_client_id: str | None = None,
        oauth_grant_id: str | None = None,
        session_generation: int | None = None,
    ) -> ActorAuthority:
        """The actor's liveness and whether it holds ``permission``, plus auth lineage."""
        has_channel_lineage = any(
            v is not None for v in (auth_token_sha256, auth_method, oauth_client_id, oauth_grant_id)
        )
        if actor_type == ActorType.AGENT.value:
            async with self._admin_db.read_session() as session:
                row = (
                    await session.execute(
                        _AGENT_AUTHORITY, {"actor_id": actor_id, "permission": permission}
                    )
                ).first()
                if row is None:
                    return _NONE
                if row.status == _OWNER_INACTIVE:
                    log_agent_owner_inactive(actor_id, row.owner_id)
                base = ActorAuthority(
                    active=row.status == "active", holds_permission=bool(row.granted)
                )
                if not base.active or not has_channel_lineage:
                    return base
                return await self._verify_channel_lineage(
                    session,
                    base=base,
                    actor_id=actor_id,
                    actor_type=actor_type,
                    permission=permission,
                    auth_token_sha256=auth_token_sha256,
                    auth_method=auth_method,
                    oauth_client_id=oauth_client_id,
                    oauth_grant_id=oauth_grant_id,
                )
        if actor_type == ActorType.USER.value:
            async with self._admin_db.read_session() as session:
                rows = (await session.execute(_USER_AUTHORITY, {"actor_id": actor_id})).all()
                if not rows:
                    return _NONE
                user_active = bool(rows[0].active)
                if session_generation is not None and int(rows[0].session_generation) != int(
                    session_generation
                ):
                    user_active = False
                granted = {row.permission for row in rows if row.permission is not None}
                base = ActorAuthority(
                    active=user_active,
                    holds_permission=permission in compute_effective(granted),
                )
                if not base.active or not has_channel_lineage:
                    return base
                return await self._verify_channel_lineage(
                    session,
                    base=base,
                    actor_id=actor_id,
                    actor_type=actor_type,
                    permission=permission,
                    auth_token_sha256=auth_token_sha256,
                    auth_method=auth_method,
                    oauth_client_id=oauth_client_id,
                    oauth_grant_id=oauth_grant_id,
                )
        return _NONE

    async def _verify_channel_lineage(
        self,
        session: AsyncSession,
        *,
        base: ActorAuthority,
        actor_id: str,
        actor_type: str,
        permission: str,
        auth_token_sha256: str | None,
        auth_method: str | None,
        oauth_client_id: str | None,
        oauth_grant_id: str | None,
    ) -> ActorAuthority:
        """Revalidate the submitting token, API key, OAuth client, and OAuth grant."""
        holds_permission = base.holds_permission
        effective_client_id = oauth_client_id
        effective_grant_id = oauth_grant_id

        if auth_token_sha256 is not None and auth_method == "jwt":
            # JWTs are self-contained: their expiry is checked by the worker,
            # and the actor/live-permission check above supplies liveness.
            pass
        elif auth_token_sha256 is not None:
            if auth_method == "api_key":
                key_row = (
                    await session.execute(
                        _AGENT_API_KEY_LINEAGE,
                        {"actor_id": actor_id, "token_hash": auth_token_sha256},
                    )
                ).first()
                if key_row is None:
                    return _NONE
            else:
                tok_row = (
                    await session.execute(_ACCESS_TOKEN_LINEAGE, {"token_hash": auth_token_sha256})
                ).one_or_none()
                if tok_row is None:
                    if auth_method is None and actor_type == ActorType.AGENT.value:
                        key_row = (
                            await session.execute(
                                _AGENT_API_KEY_LINEAGE,
                                {"actor_id": actor_id, "token_hash": auth_token_sha256},
                            )
                        ).first()
                        if key_row is None:
                            return _NONE
                    else:
                        return _NONE
                else:
                    if tok_row.actor_id != actor_id or tok_row.actor_type != actor_type:
                        return _NONE
                    now = datetime.now(UTC)
                    expires_at = _as_aware_datetime(tok_row.expires_at)
                    revoked_at = (
                        _as_aware_datetime(tok_row.revoked_at)
                        if tok_row.revoked_at is not None
                        else None
                    )
                    if revoked_at is not None or expires_at <= now:
                        return _NONE
                    token_perms = set(scopes_to_permissions(_as_scope_list(tok_row.scopes)))
                    if (
                        bool(tok_row.is_ephemeral) or actor_type != ActorType.AGENT.value
                    ) and permission not in token_perms:
                        holds_permission = False
                    if effective_client_id is None and tok_row.oauth_client_id is not None:
                        effective_client_id = str(tok_row.oauth_client_id)
                    if effective_grant_id is None and tok_row.oauth_grant_id is not None:
                        effective_grant_id = str(tok_row.oauth_grant_id)

        if effective_client_id is not None:
            client_row = (
                await session.execute(_OAUTH_CLIENT_LINEAGE, {"client_id": effective_client_id})
            ).one_or_none()
            if client_row is None or not bool(client_row.active) or not bool(client_row.approved):
                return _NONE
            if client_row.allowed_scopes is not None and permission not in _as_scope_list(
                client_row.allowed_scopes
            ):
                holds_permission = False

        if effective_grant_id is not None:
            grant_row = (
                await session.execute(_OAUTH_GRANT_LINEAGE, {"grant_id": effective_grant_id})
            ).one_or_none()
            if grant_row is None or not bool(grant_row.active):
                return _NONE
            if actor_type == ActorType.AGENT.value and grant_row.agent_id != actor_id:
                return _NONE
            if grant_row.scopes is not None and permission not in _as_scope_list(grant_row.scopes):
                holds_permission = False

        return ActorAuthority(active=True, holds_permission=holds_permission)
