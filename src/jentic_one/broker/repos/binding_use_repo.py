"""Record that an agent used a credential binding (admin DB, raw SQL).

The broker may not import the admin ORM, so like the binding resolver this
runs as raw SQL. ``last_used_at`` restarts a binding's idle clock; it is
written at most once per interval (an hour by default), so tracking use
never turns every broker call into a row write.

A binding with a use count (``max_quota``) is different: every call takes a
use with one atomic decrement, so two concurrent calls can never both take
the last one.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_RECORD_USE = text(
    # A use also clears the idle warning's marker: the next idle period may warn again.
    "UPDATE agent_credential_bindings SET last_used_at = :now, idle_warned_at = NULL "
    "WHERE agent_id = :agent_id AND credential_id = :credential_id "
    "AND (last_used_at IS NULL OR last_used_at <= :stale_before)"
)


_USE_COUNT = text(
    "SELECT max_quota FROM agent_credential_bindings "
    "WHERE agent_id = :agent_id AND credential_id = :credential_id"
)

_AFTER_EXPIRY = text(
    "SELECT after_expiry FROM agent_credential_bindings "
    "WHERE agent_id = :agent_id AND credential_id = :credential_id"
)

_TAKE_USE = text(
    # Taking a use is also a use: it restarts the idle clock without the throttle.
    "UPDATE agent_credential_bindings "
    "SET quota_remaining = quota_remaining - 1, last_used_at = :now, idle_warned_at = NULL "
    "WHERE agent_id = :agent_id AND credential_id = :credential_id "
    "AND max_quota IS NOT NULL AND quota_remaining > 0"
)


class BindingUseRepository:
    """Flush-only writes of a binding's use; the caller owns the transaction."""

    @staticmethod
    async def record_use(
        session: AsyncSession,
        *,
        agent_id: str,
        credential_id: str,
        now: datetime,
        min_interval: timedelta,
    ) -> bool:
        """Stamp ``last_used_at = now`` unless it was stamped within ``min_interval``.

        Returns whether a row changed. A binding the agent no longer holds
        (or a caller that is not an agent) changes nothing.
        """
        result = await session.execute(
            _RECORD_USE,
            {
                "now": now,
                "agent_id": agent_id,
                "credential_id": credential_id,
                "stale_before": now - min_interval,
            },
        )
        return bool(getattr(result, "rowcount", 0))

    @staticmethod
    async def take_use(
        session: AsyncSession, *, agent_id: str, credential_id: str, now: datetime
    ) -> bool:
        """Take one use from the binding's count; ``False`` when none is left.

        A binding without a use count (or no binding at all) has nothing to
        take and always allows: whether the binding authorizes the call at all
        is the resolver's decision.
        """
        params = {"agent_id": agent_id, "credential_id": credential_id}
        row = (await session.execute(_USE_COUNT, params)).first()
        if row is None or row.max_quota is None:
            return True
        result = await session.execute(_TAKE_USE, {**params, "now": now})
        return bool(getattr(result, "rowcount", 0))

    @staticmethod
    async def after_expiry(
        session: AsyncSession, *, agent_id: str, credential_id: str
    ) -> str | None:
        """What a call gets once the binding ends (``hold`` or ``refuse``); ``None`` if gone."""
        row = (
            await session.execute(
                _AFTER_EXPIRY, {"agent_id": agent_id, "credential_id": credential_id}
            )
        ).first()
        return None if row is None else str(row.after_expiry)
