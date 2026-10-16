"""Actor liveness and permission lookup for work that runs after the request is gone.

The sync execute path learns whether its caller is still active — and still
holds the execute permission — from credential resolution (``InProcessTokenResolver``
/ ``ApiKeyResolver`` re-read the actor row and, for agents, the live
``actor_permission_grants`` on every resolve). A queued execution
has no credential to resolve when the worker picks it up, so this answers the
same questions directly from the actor rows, in **one** round-trip per check:

- ``agent`` → active when ``agents.status = 'active'`` (suspended/archived/
  missing → inactive); holds a permission when the exact
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

Raw SQL against the admin schema — the broker may not import the ``admin`` ORM.
"""

from __future__ import annotations

from sqlalchemy import Boolean, String, text

from jentic_one.shared.auth.permission_catalog import compute_effective
from jentic_one.shared.db import DatabaseSession
from jentic_one.shared.jobs.protocols import ActorAuthority
from jentic_one.shared.models import ActorType

_AGENT_AUTHORITY = text(
    "SELECT a.status,"
    " EXISTS (SELECT 1 FROM actor_permission_grants g"
    "  WHERE g.actor_id = a.id AND g.actor_type = 'agent' AND g.permission = :permission)"
    " AS granted"
    " FROM agents a WHERE a.id = :actor_id"
).columns(status=String, granted=Boolean)
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
