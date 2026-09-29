"""Actor liveness lookup for work that runs after the inbound request is gone.

The sync execute path learns whether its caller is still active from token
resolution (``InProcessTokenResolver`` / ``ApiKeyResolver`` re-read the actor
row on every resolve). A queued execution has no token to resolve when the
worker picks it up, so this answers the same question directly from the actor
row, with the same semantics:

- ``agent`` → ``agents.status = 'active'`` (suspended/archived/missing → inactive);
- ``user`` → ``users.active`` (disabled/missing → inactive);
- anything else (retired ``service_account`` / ``toolkit`` identities) → inactive.

Raw SQL against the admin schema — the broker may not import the ``admin`` ORM.
"""

from __future__ import annotations

from sqlalchemy import Boolean, text

from jentic_one.shared.db import DatabaseSession
from jentic_one.shared.models import ActorType

_AGENT_STATUS = text("SELECT status FROM agents WHERE id = :actor_id")
_USER_ACTIVE = text("SELECT active FROM users WHERE id = :actor_id").columns(active=Boolean)


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
        return False
