"""Repository for ConnectSession CRUD operations."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.control.core.schema.connect_sessions import TARGET_KIND_VENDOR, ConnectSession

# The non-terminal states. A session outside these is frozen — no transition
# (including the terminal sweep) may touch it. ``awaiting_app`` is an OAuth
# API target with no app resolved yet; it holds a pending credential like
# ``created``.
LIVE_STATES: tuple[str, ...] = ("created", "awaiting_app", "polling")


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
        poll_token_hash: str,
        target_kind: str = TARGET_KIND_VENDOR,
        requested_scopes: list[str] | None = None,
        requested_permission_rules: list[dict[str, Any]] | None = None,
        preferred_flow: str | None = None,
        reason: str | None = None,
        created_by: str | None = None,
    ) -> ConnectSession:
        row = ConnectSession(
            credential_id=credential_id,
            target_kind=target_kind,
            vendor=vendor,
            agent_id=agent_id,
            initiator_actor_id=initiator_actor_id,
            state=state,
            resolved_flow=resolved_flow,
            poll_token_hash=poll_token_hash,
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
    async def list_all(
        session: AsyncSession,
        *,
        cursor: tuple[datetime, str] | None = None,
        limit: int = 50,
        state: str | None = None,
        vendor: str | None = None,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> list[ConnectSession]:
        """List connect sessions with keyset pagination (created_at, id).

        Fetches ``limit + 1`` rows so the caller can detect ``has_more``
        (same contract as ``CredentialRepository.list_all``). ``filters``
        carries the caller's pre-built access-scoping expressions — this
        repo never sees ``Identity``.
        """
        stmt = select(ConnectSession).order_by(
            ConnectSession.created_at.desc(), ConnectSession.id.desc()
        )
        if state is not None:
            stmt = stmt.where(ConnectSession.state == state)
        if vendor is not None:
            stmt = stmt.where(ConnectSession.vendor == vendor)
        if cursor is not None:
            cursor_ts, cursor_id = cursor
            stmt = stmt.where(
                (ConnectSession.created_at < cursor_ts)
                | ((ConnectSession.created_at == cursor_ts) & (ConnectSession.id < cursor_id))
            )
        if filters is not None:
            for f in filters:
                stmt = stmt.where(f)
        stmt = stmt.limit(limit + 1)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_live_by_credential(
        session: AsyncSession, credential_id: str
    ) -> ConnectSession | None:
        """Return a non-terminal ``ConnectSession`` wrapping the credential, if any.

        Used by the connect-poll scanner to dispatch: a candidate credential
        with a live session takes the session-mode advancement path, one
        without takes the raw-credential path. Only sessions in
        ``LIVE_STATES`` count (terminal states are already frozen).
        """
        stmt = select(ConnectSession).where(
            ConnectSession.credential_id == credential_id,
            ConnectSession.state.in_(LIVE_STATES),
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
        flows: Sequence[str] | None = None,
        exclude_flows: Sequence[str] | None = None,
    ) -> list[str]:
        """Return ids of live sessions created before ``older_than``.

        Feeds the TTL sweep: sessions whose initiator never confirmed, or
        whose auth-code popup was abandoned, have no other expiry driver (the
        device-flow scanner only sees rows with an aux device-code row), so
        they — and their upfront ``pending`` credential rows — would
        otherwise leak forever. ``flows`` / ``exclude_flows`` narrow the
        sweep by ``resolved_flow`` so each flow family gets its own cutoff.
        """
        stmt = select(ConnectSession.id).where(
            ConnectSession.state.in_(LIVE_STATES),
            ConnectSession.created_at < older_than,
        )
        if flows is not None:
            stmt = stmt.where(ConnectSession.resolved_flow.in_(flows))
        if exclude_flows is not None:
            stmt = stmt.where(ConnectSession.resolved_flow.not_in(exclude_flows))
        stmt = stmt.order_by(ConnectSession.created_at.asc()).limit(limit)
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
