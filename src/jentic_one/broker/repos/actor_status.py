"""Actor liveness and permission lookup for work that runs after the request is gone.

The sync execute path learns whether its caller is still active — and still
holds the execute permission — from credential resolution (``InProcessTokenResolver``
/ ``ApiKeyResolver`` re-read the actor row and, for agents, the live
``actor_permission_grants`` on every resolve). A queued execution
has no credential to resolve when the worker picks it up, so this answers the
same questions directly from the actor rows, with the same semantics:

- ``agent`` → ``agents.status = 'active'`` (suspended/archived/missing → inactive)
  and, for an owned agent, the owner's ``users.active`` (an owned agent follows
  its owner's account status; ownerless agents depend on their own status);
- ``user`` → ``users.active`` and ``users.deleted_at IS NULL``
  (disabled/deleted/missing → inactive), read through the
  shared ``read_user_status`` the login-JWT verifier uses;
- anything else → inactive. That covers the retired ``toolkit`` and
  ``service_account`` identities (theme 8 Phase 4 dropped the
  service-account tables; a job queued under one can never run).

The verdict is terminal for the work being checked. A queued job or approved
hold that comes up while its agent's owner is disabled is refused, not
deferred: re-enabling the owner restores new requests, and refused work must
be submitted again.

The broker's self-contained JWT path asks the same question (behind a
short-TTL cache) once a signed agent token's claims check out.

Raw SQL against the admin schema — the broker may not import the ``admin`` ORM.
"""

from __future__ import annotations

from sqlalchemy import text

from jentic_one.shared.auth.actor_status import read_user_status
from jentic_one.shared.auth.owner_status import log_agent_owner_inactive
from jentic_one.shared.db import DatabaseSession
from jentic_one.shared.models import ActorType

#: ``status`` of an active, owned agent whose owner is not active.
_OWNER_INACTIVE = "owner_inactive"
# The same owner clause as the broker's ``InProcessTokenResolver``: a queued
# job or approved hold of an owned agent must not run while the owner is
# disabled or deleted. A soft-deleted user has ``active`` false, so ``o.active``
# covers delete too. One PK probe on agents, one on users.
_AGENT_STATUS = text(
    "SELECT CASE"
    " WHEN a.status <> 'active' THEN a.status"
    " WHEN a.owner_id IS NULL THEN 'active'"
    " WHEN EXISTS (SELECT 1 FROM users o WHERE o.id = a.owner_id AND o.active) THEN 'active'"
    f" ELSE '{_OWNER_INACTIVE}' END AS status, a.owner_id"
    " FROM agents a WHERE a.id = :actor_id"
)
_PERMISSION_GRANTED = text(
    "SELECT 1 FROM actor_permission_grants"
    " WHERE actor_id = :actor_id AND actor_type = :actor_type AND permission = :permission"
)


class ActorStatusResolver:
    """Reports whether an actor is currently allowed to act (fails closed)."""

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
            status = await read_user_status(self._admin_db, actor_id)
            return status is not None and status.accepts_credentials
        return False

    async def holds_permission(self, *, actor_id: str, actor_type: str, permission: str) -> bool:
        """Whether the actor still holds ``permission``, where permissions are live grants.

        Agents → the ``actor_permission_grants`` row must exist (a revoked grant
        fails closed). Users → ``True``: their permissions ride on the user's own
        token, which the worker does not hold, so there is no run-time grant to
        re-read (their liveness is still checked by :meth:`is_active`). Any
        other actor type (the retired ``toolkit`` / ``service_account``
        identities) → ``False``: fail closed.
        """
        if actor_type == ActorType.USER.value:
            return True
        if actor_type != ActorType.AGENT.value:
            return False
        async with self._admin_db.session() as session:
            row = (
                await session.execute(
                    _PERMISSION_GRANTED,
                    {"actor_id": actor_id, "actor_type": actor_type, "permission": permission},
                )
            ).first()
        return row is not None
