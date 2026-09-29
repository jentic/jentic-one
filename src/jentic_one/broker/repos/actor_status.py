"""Actor liveness and scope lookup for work that runs after the request is gone.

The sync execute path learns whether its caller is still active — and still
holds the execute scope — from credential resolution (``InProcessTokenResolver``
/ ``ApiKeyResolver`` re-read the actor row and, for agents and service
accounts, the live ``actor_scope_grants`` on every resolve). A queued execution
has no credential to resolve when the worker picks it up, so this answers the
same questions directly from the actor rows, with the same semantics:

- ``agent`` → ``agents.status = 'active'`` (suspended/archived/missing → inactive);
- ``user`` → ``users.active`` (disabled/missing → inactive);
- ``service_account`` → ``service_accounts.status = 'active'`` and not yet
  migrated (``migrated_to_actor_id IS NULL``) — the ``ApiKeyResolver`` fallback
  arm that still serves unmigrated ``sak_`` / ``jntc_live_`` keys; a migrated
  account resolves as its successor agent, so its own row never authorizes;
- anything else (retired ``toolkit`` identities) → inactive.

Raw SQL against the admin schema — the broker may not import the ``admin`` ORM.
"""

from __future__ import annotations

from sqlalchemy import Boolean, text

from jentic_one.shared.db import DatabaseSession
from jentic_one.shared.models import ActorType

_AGENT_STATUS = text("SELECT status FROM agents WHERE id = :actor_id")
_USER_ACTIVE = text("SELECT active FROM users WHERE id = :actor_id").columns(active=Boolean)
_SERVICE_ACCOUNT_ACTIVE = text(
    "SELECT 1 FROM service_accounts"
    " WHERE id = :actor_id AND status = 'active' AND migrated_to_actor_id IS NULL"
)
_SCOPE_GRANTED = text(
    "SELECT 1 FROM actor_scope_grants"
    " WHERE actor_id = :actor_id AND actor_type = :actor_type AND scope = :scope"
)

# Actor kinds whose scopes resolve live from ``actor_scope_grants`` on the sync
# path (API keys and long-lived tokens). User scopes are carried by the user's
# own token, so there is no per-actor grant row to re-read for them.
_LIVE_GRANT_ACTOR_TYPES = frozenset({ActorType.AGENT.value, ActorType.SERVICE_ACCOUNT.value})


class ActorStatusResolver:
    """Reports whether an actor is currently allowed to act (fails closed)."""

    def __init__(self, admin_db: DatabaseSession) -> None:
        self._admin_db = admin_db

    async def is_active(self, *, actor_id: str, actor_type: str) -> bool:
        """``True`` only when the actor row exists and is active."""
        if actor_type == ActorType.AGENT.value:
            async with self._admin_db.session() as session:
                status = (
                    await session.execute(_AGENT_STATUS, {"actor_id": actor_id})
                ).scalar_one_or_none()
            return status == "active"
        if actor_type == ActorType.USER.value:
            async with self._admin_db.session() as session:
                active = (
                    await session.execute(_USER_ACTIVE, {"actor_id": actor_id})
                ).scalar_one_or_none()
            return bool(active)
        if actor_type == ActorType.SERVICE_ACCOUNT.value:
            async with self._admin_db.session() as session:
                row = (
                    await session.execute(_SERVICE_ACCOUNT_ACTIVE, {"actor_id": actor_id})
                ).first()
            return row is not None
        return False

    async def holds_scope(self, *, actor_id: str, actor_type: str, scope: str) -> bool:
        """Whether the actor still holds ``scope``, where scopes are live grants.

        Agents and service accounts → the ``actor_scope_grants`` row must exist
        (a revoked grant fails closed). Users → ``True``: their scopes ride on
        the user's own token, which the worker does not hold, so there is no
        run-time grant to re-read (their liveness is still checked by
        :meth:`is_active`).
        """
        if actor_type not in _LIVE_GRANT_ACTOR_TYPES:
            return True
        async with self._admin_db.session() as session:
            row = (
                await session.execute(
                    _SCOPE_GRANTED,
                    {"actor_id": actor_id, "actor_type": actor_type, "scope": scope},
                )
            ).first()
        return row is not None
