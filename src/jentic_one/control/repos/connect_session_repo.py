"""Repository for ConnectSession CRUD operations."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.control.core.schema.connect_sessions import ConnectSession


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
