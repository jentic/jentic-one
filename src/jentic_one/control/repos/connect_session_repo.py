"""Repository for ConnectSession CRUD operations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.control.core.schema.connect_sessions import ConnectSession

# The two non-terminal states. A session outside these is frozen — no
# transition (including the terminal sweep) may touch it.
LIVE_STATES: tuple[str, ...] = ("created", "polling")


class ConnectSessionRepository:
    """Data access layer for ConnectSession — flush-only, never commits."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        credential_id: str,
        vendor: str,
        agent_id: str | None,
        initiator_actor_id: str,
        state: str,
        resolved_flow: str,
        poll_token: str,
        requested_scopes: list[str] | None = None,
        requested_permission_rules: list[dict[str, Any]] | None = None,
        preferred_flow: str | None = None,
        reason: str | None = None,
        created_by: str | None = None,
    ) -> ConnectSession:
        row = ConnectSession(
            credential_id=credential_id,
            vendor=vendor,
            agent_id=agent_id,
            initiator_actor_id=initiator_actor_id,
            state=state,
            resolved_flow=resolved_flow,
            poll_token=poll_token,
            requested_scopes=requested_scopes or [],
            requested_permission_rules=requested_permission_rules or [],
            preferred_flow=preferred_flow,
            reason=reason,
            created_by=created_by,
        )
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    async def get_by_id(session: AsyncSession, session_id: str) -> ConnectSession | None:
        stmt = select(ConnectSession).where(ConnectSession.id == session_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_poll_token(session: AsyncSession, poll_token: str) -> ConnectSession | None:
        stmt = select(ConnectSession).where(ConnectSession.poll_token == poll_token)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_live_by_credential(
        session: AsyncSession, credential_id: str
    ) -> ConnectSession | None:
        """Return a non-terminal ``ConnectSession`` wrapping the credential, if any.

        Used by the connect-poll scanner to dispatch: a candidate credential
        with a live session takes the session-mode advancement path, one
        without takes the raw-credential path. Only ``polling`` and
        ``created`` sessions count as "live" here (terminal states are
        already frozen).
        """
        stmt = select(ConnectSession).where(
            ConnectSession.credential_id == credential_id,
            ConnectSession.state.in_(("created", "polling")),
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def transition_state(
        session: AsyncSession,
        session_id: str,
        *,
        to_state: str,
        from_states: tuple[str, ...],
        **fields: Any,
    ) -> bool:
        """Compare-and-swap the session state; extra ``fields`` ride the same UPDATE.

        Returns True when the row was in one of ``from_states`` and the
        transition (plus any extra field writes) was applied; False when the
        row is missing or already moved on — the caller must then treat the
        operation as lost to a concurrent winner and leave the row alone.
        This is the single line of defence against terminal races (callback
        replay, multi-pod scanner overlap, concurrent confirms).
        """
        stmt = (
            update(ConnectSession)
            .where(
                ConnectSession.id == session_id,
                ConnectSession.state.in_(from_states),
            )
            .values(state=to_state, **fields)
        )
        result = await session.execute(stmt)
        await session.flush()
        return bool(getattr(result, "rowcount", 0))

    @staticmethod
    async def list_stale_live_ids(
        session: AsyncSession,
        *,
        older_than: datetime,
        limit: int,
    ) -> list[str]:
        """Return ids of live (``created``/``polling``) sessions created before ``older_than``.

        Feeds the flow-agnostic TTL sweep: sessions whose initiator never
        confirmed, or whose auth-code popup was abandoned, have no other
        expiry driver (the device-flow scanner only sees rows with an aux
        device-code row), so they — and their upfront ``pending`` credential
        rows — would otherwise leak forever.
        """
        stmt = (
            select(ConnectSession.id)
            .where(
                ConnectSession.state.in_(LIVE_STATES),
                ConnectSession.created_at < older_than,
            )
            .order_by(ConnectSession.created_at.asc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return [str(row_id) for row_id in result.scalars().all()]

    @staticmethod
    async def update_fields(
        session: AsyncSession, session_id: str, **fields: Any
    ) -> ConnectSession | None:
        """Apply a partial update; caller responsible for legal state transitions."""
        row = await ConnectSessionRepository.get_by_id(session, session_id)
        if row is None:
            return None
        for key, value in fields.items():
            setattr(row, key, value)
        await session.flush()
        return row
