"""Repository for ConnectSession CRUD operations."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.control.core.schema.connect_sessions import (
    TARGET_KIND_API,
    TARGET_KIND_VENDOR,
    ConnectSession,
)

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
        api_name: str | None = None,
        api_version: str | None = None,
        scheme_type: str | None = None,
        scheme_location: str | None = None,
        scheme_field_name: str | None = None,
        pinned_hosts: list[str] | None = None,
        vendor_key: str | None = None,
        dedupe_key: str | None = None,
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
            api_name=api_name,
            api_version=api_version,
            scheme_type=scheme_type,
            scheme_location=scheme_location,
            scheme_field_name=scheme_field_name,
            pinned_hosts=pinned_hosts,
            vendor_key=vendor_key,
            dedupe_key=dedupe_key,
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
        target_kind: str | None = None,
        exclude_target_kind: str | None = None,
    ) -> list[str]:
        """Return ids of live sessions created before ``older_than``.

        Feeds the TTL sweep: sessions whose initiator never confirmed, or
        whose auth-code popup was abandoned, have no other expiry driver (the
        device-flow scanner only sees rows with an aux device-code row), so
        they — and their upfront ``pending`` credential rows — would
        otherwise leak forever. ``flows`` / ``exclude_flows`` narrow the
        sweep by ``resolved_flow`` so each flow family gets its own cutoff;
        ``target_kind`` / ``exclude_target_kind`` narrow it by target kind.
        """
        stmt = select(ConnectSession.id).where(
            ConnectSession.state.in_(LIVE_STATES),
            ConnectSession.created_at < older_than,
        )
        if flows is not None:
            stmt = stmt.where(ConnectSession.resolved_flow.in_(flows))
        if exclude_flows is not None:
            stmt = stmt.where(ConnectSession.resolved_flow.not_in(exclude_flows))
        if target_kind is not None:
            stmt = stmt.where(ConnectSession.target_kind == target_kind)
        if exclude_target_kind is not None:
            stmt = stmt.where(ConnectSession.target_kind != exclude_target_kind)
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

    @staticmethod
    async def get_open_api_target(
        session: AsyncSession,
        *,
        agent_id: str,
        vendor: str,
        api_name: str,
        api_version: str,
    ) -> ConnectSession | None:
        """The agent's open session for an API identity (the dedupe index's key)."""
        stmt = select(ConnectSession).where(
            ConnectSession.target_kind == TARGET_KIND_API,
            ConnectSession.state.in_(LIVE_STATES),
            ConnectSession.agent_id == agent_id,
            ConnectSession.vendor == vendor,
            ConnectSession.api_name == api_name,
            ConnectSession.api_version == api_version,
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def get_open_vendor_target(
        session: AsyncSession, *, agent_id: str, vendor: str, dedupe_key: str
    ) -> ConnectSession | None:
        """The agent's open session for the same OAuth request (the dedupe index's key)."""
        stmt = select(ConnectSession).where(
            ConnectSession.target_kind == TARGET_KIND_VENDOR,
            ConnectSession.state.in_(LIVE_STATES),
            ConnectSession.agent_id == agent_id,
            ConnectSession.vendor == vendor,
            ConnectSession.dedupe_key == dedupe_key,
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def rotate_poll_token(
        session: AsyncSession, session_id: str, *, poll_token_hash: str
    ) -> bool:
        """Replace an open session's poll-token digest; False when it is no longer open."""
        stmt = (
            update(ConnectSession)
            .where(ConnectSession.id == session_id, ConnectSession.state.in_(LIVE_STATES))
            .values(poll_token_hash=poll_token_hash)
        )
        result = await session.execute(stmt)
        await session.flush()
        return bool(getattr(result, "rowcount", 0))

    @staticmethod
    async def count_open(
        session: AsyncSession,
        *,
        agent_ids: Sequence[str] = (),
        initiator_actor_id: str | None = None,
    ) -> int:
        """Open sessions naming one of ``agent_ids`` or started by ``initiator_actor_id``."""
        clauses: list[ColumnElement[bool]] = []
        if agent_ids:
            clauses.append(ConnectSession.agent_id.in_(list(agent_ids)))
        if initiator_actor_id is not None:
            clauses.append(ConnectSession.initiator_actor_id == initiator_actor_id)
        if not clauses:
            return 0
        stmt = (
            select(func.count())
            .select_from(ConnectSession)
            .where(ConnectSession.state.in_(LIVE_STATES), or_(*clauses))
        )
        result = await session.execute(stmt)
        return int(result.scalar_one())

    @staticmethod
    async def list_awaiting_app(session: AsyncSession, *, limit: int) -> list[ConnectSession]:
        """Sessions waiting for an OAuth app, oldest first."""
        stmt = (
            select(ConnectSession)
            .where(ConnectSession.state == "awaiting_app")
            .order_by(ConnectSession.created_at.asc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())
