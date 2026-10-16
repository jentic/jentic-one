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
- ``user`` → active when ``users.active`` (disabled/deleted/missing →
  inactive); holds a permission when the implication-expanded
  (``compute_effective``) set of its live ``user_permission_grants`` contains
  it — the same effective set a user's login token is minted with, so
  ``org:admin`` implies the execute permission. Read live, a revocation after
  enqueue applies to the queued job even while the user's token would still
  carry the permission until it expires;
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

from sqlalchemy import Boolean, String, text

from jentic_one.shared.auth.owner_status import log_agent_owner_inactive
from jentic_one.shared.auth.permission_catalog import compute_effective
from jentic_one.shared.db import DatabaseSession
from jentic_one.shared.jobs.protocols import ActorAuthority
from jentic_one.shared.models import ActorType

#: ``status`` of an active, owned agent whose owner is not active.
_OWNER_INACTIVE = "owner_inactive"
# The same owner clause as the broker's ``InProcessTokenResolver``: a queued
# job or approved hold of an owned agent must not run while the owner is
# disabled or deleted. A soft-deleted user has ``active`` false, so ``o.active``
# covers delete too. One PK probe on agents, one on users.
_AGENT_STATUS_SQL = (
    "CASE"
    " WHEN a.status <> 'active' THEN a.status"
    " WHEN a.owner_id IS NULL THEN 'active'"
    " WHEN EXISTS (SELECT 1 FROM users o WHERE o.id = a.owner_id AND o.active) THEN 'active'"
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
_USER_ACTIVE = text("SELECT active FROM users WHERE id = :actor_id").columns(active=Boolean)
_USER_AUTHORITY = text(
    "SELECT u.active, g.permission FROM users u"
    " LEFT JOIN user_permission_grants g ON g.user_id = u.id"
    " WHERE u.id = :actor_id"
).columns(active=Boolean, permission=String)

_NONE = ActorAuthority(active=False, holds_permission=False)


class ActorStatusResolver:
    """Reports whether an actor may currently act (fails closed).

    Implements the shared ``ActorStatusReader`` protocol.
    """

    def __init__(self, admin_db: DatabaseSession) -> None:
        self._admin_db = admin_db

    async def is_active(self, *, actor_id: str, actor_type: str) -> bool:
        """``True`` only when the actor row exists and is active."""
        if actor_type == ActorType.AGENT.value:
            async with self._admin_db.session() as session:
                row = (await session.execute(_AGENT_STATUS, {"actor_id": actor_id})).one_or_none()
            if row is None:
                return False
            if row.status == _OWNER_INACTIVE:
                log_agent_owner_inactive(actor_id, row.owner_id)
            return bool(row.status == "active")
        if actor_type == ActorType.USER.value:
            async with self._admin_db.session() as session:
                active = (
                    await session.execute(_USER_ACTIVE, {"actor_id": actor_id})
                ).scalar_one_or_none()
            return bool(active)
        return False

    async def authority(self, *, actor_id: str, actor_type: str, permission: str) -> ActorAuthority:
        """The actor's liveness and whether it holds ``permission``, from one query."""
        if actor_type == ActorType.AGENT.value:
            async with self._admin_db.session() as session:
                row = (
                    await session.execute(
                        _AGENT_AUTHORITY, {"actor_id": actor_id, "permission": permission}
                    )
                ).first()
            if row is None:
                return _NONE
            if row.status == _OWNER_INACTIVE:
                log_agent_owner_inactive(actor_id, row.owner_id)
            return ActorAuthority(active=row.status == "active", holds_permission=bool(row.granted))
        if actor_type == ActorType.USER.value:
            async with self._admin_db.session() as session:
                rows = (await session.execute(_USER_AUTHORITY, {"actor_id": actor_id})).all()
            if not rows:
                return _NONE
            granted = {row.permission for row in rows if row.permission is not None}
            return ActorAuthority(
                active=bool(rows[0].active),
                holds_permission=permission in compute_effective(granted),
            )
        return _NONE
