"""Cross-database reads against admin tables for the control module.

Existence checks and cross-DB lookups share one seam: raw SQL (text()) so the
control module never imports admin ORM models.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, NamedTuple

from sqlalchemy import Boolean, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import BindParameter

from jentic_one.shared.db.types import UTCDateTime


class CredentialBoundAgentRow(NamedTuple):
    """Result row for agents directly bound to a credential (theme 5 phase 1)."""

    binding_id: str
    agent_id: str
    agent_name: str
    agent_status: str
    bound_at: datetime
    suspended: bool
    rule_set_id: str | None


class AgentVisibility(NamedTuple):
    """Which bound agents a caller may see: itself, or agents owned by ``owner_ids``."""

    self_id: str
    owner_ids: tuple[str, ...]


class AgentCredentialBindingRow(NamedTuple):
    """Direct agent↔credential binding existence-check row (theme 5 phase 1)."""

    binding_id: str
    suspended: bool
    rule_set_id: str | None


class PrerequisiteRepository:
    """Cross-DB reads (existence checks + lookups) without admin imports."""

    @staticmethod
    async def active_user_exists(session: AsyncSession, *, user_id: str) -> bool:
        """Return True if an active user with this id exists (admin DB).

        A cross-DB existence check that lets a control-side caller validate a
        user id without importing admin ORM models (e.g. before recording a
        reference to a user). Returns False for unknown or deactivated users.
        """
        result = await session.execute(
            text("SELECT 1 FROM users WHERE id = :user_id AND active = true LIMIT 1"),
            {"user_id": user_id},
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def filter_user_ids_with_permission(
        session: AsyncSession, *, user_ids: Sequence[str], permission: str
    ) -> set[str]:
        """Return the subset of ``user_ids`` holding ``permission`` as a direct grant (admin DB)."""
        if not user_ids:
            return set()
        result = await session.execute(
            text(
                "SELECT DISTINCT user_id FROM user_permission_grants "
                "WHERE permission = :permission AND user_id IN :user_ids"
            ).bindparams(bindparam("user_ids", expanding=True)),
            {"permission": permission, "user_ids": list(user_ids)},
        )
        return {str(row[0]) for row in result.fetchall()}

    @staticmethod
    async def list_credential_ids_for_agent(session: AsyncSession, *, agent_id: str) -> list[str]:
        """Return the credential ids the agent is directly and actively bound to.

        Read-scoping seam (theme 5 phase 1): an agent must be able to read a
        credential it is bound to even when it owns nothing — same
        orphaned-agent rationale as issues #665/#682. Suspended bindings
        grant no visibility: a suspension is a cut-off, so the agent keeps
        seeing the binding (with its flag) in ``/me`` but loses the widened
        read of the credential itself until resumed. Runs against an admin
        session and returns plain ids for ``build_access_filters`` (the
        control scoping module must not import admin ORM models or query
        across databases).
        """
        result = await session.execute(
            text(
                "SELECT credential_id FROM agent_credential_bindings "
                "WHERE agent_id = :agent_id AND suspended = false"
            ),
            {"agent_id": agent_id},
        )
        return [row[0] for row in result.fetchall()]

    @staticmethod
    async def list_agents_for_credential(
        session: AsyncSession,
        *,
        credential_id: str,
        cursor: tuple[datetime, str] | None = None,
        limit: int = 50,
        visible_to: AgentVisibility | None = None,
    ) -> list[CredentialBoundAgentRow]:
        """Return agents directly bound to a credential, paginated by (bound_at DESC, id DESC).

        The reverse lookup behind ``GET /credentials/{id}/agents`` (theme 5
        phase 1), reading ``agent_credential_bindings``. Suspended bindings
        are included (with their flag) so the
        credential-detail view can show a reversible cut-off, not hide it.

        ``visible_to`` narrows the rows to agents the caller may see (see
        :class:`AgentVisibility`); ``None`` returns every bound agent.
        """
        conditions = ["b.credential_id = :credential_id"]
        params: dict[str, object] = {"credential_id": credential_id, "limit": limit}
        bind_params: list[BindParameter[Any]] = []
        if cursor is not None:
            cursor_ts, cursor_id = cursor
            conditions.append(
                "(b.bound_at < :cursor_ts OR (b.bound_at = :cursor_ts AND b.id < :cursor_id))"
            )
            params.update(cursor_ts=cursor_ts, cursor_id=cursor_id)
            # Typed so SQLite compares against the stored timestamp format.
            bind_params.append(bindparam("cursor_ts", type_=UTCDateTime()))
        if visible_to is not None:
            conditions.append("(a.id = :self_id OR a.owner_id IN :owner_ids)")
            params.update(self_id=visible_to.self_id, owner_ids=list(visible_to.owner_ids))
            bind_params.append(bindparam("owner_ids", expanding=True))

        stmt = text(
            "SELECT b.id, a.id, a.name, a.status, b.bound_at, b.suspended, b.rule_set_id "
            "FROM agent_credential_bindings b "
            "JOIN agents a ON a.id = b.agent_id "
            f"WHERE {' AND '.join(conditions)} "
            "ORDER BY b.bound_at DESC, b.id DESC "
            "LIMIT :limit"
        )
        if bind_params:
            stmt = stmt.bindparams(*bind_params)
        # Typed result columns: SQLite returns raw strings and integers otherwise.
        typed = stmt.columns(bound_at=UTCDateTime(), suspended=Boolean())
        result = await session.execute(typed, params)
        return [CredentialBoundAgentRow(*row) for row in result.fetchall()]

    @staticmethod
    async def get_agent_credential_binding(
        session: AsyncSession, *, agent_id: str, credential_id: str
    ) -> AgentCredentialBindingRow | None:
        """Return the direct binding's (id, suspended, rule_set_id), or ``None``.

        Existence check for the per-binding permission endpoints (theme 5
        phase 1): the binding row lives in the admin DB while the rules live
        in the control DB, so the rules endpoints bridge the same seam the
        reverse lookup above does. ``rule_set_id`` rides along so the dry-run
        endpoint can evaluate an attached shared set instead of inline rules.
        """
        result = await session.execute(
            text(
                "SELECT id, suspended, rule_set_id FROM agent_credential_bindings "
                "WHERE agent_id = :agent_id AND credential_id = :credential_id"
            ),
            {"agent_id": agent_id, "credential_id": credential_id},
        )
        row = result.fetchone()
        if row is None:
            return None
        return AgentCredentialBindingRow(
            binding_id=str(row[0]),
            suspended=bool(row[1]),
            rule_set_id=str(row[2]) if row[2] is not None else None,
        )

    @staticmethod
    async def set_binding_rule_set(
        session: AsyncSession,
        *,
        agent_id: str,
        credential_id: str,
        rule_set_id: str | None,
    ) -> bool:
        """Point a direct binding at a shared rule set (or back to inline rules).

        Cross-DB write (control surface → admin table), same raw-SQL seam as
        the reads above. ``None`` detaches:
        the binding's inline ``agent_permission_rules`` rows apply again.
        Returns ``False`` when no such binding exists.
        """
        result = await session.execute(
            text(
                "UPDATE agent_credential_bindings SET rule_set_id = :rule_set_id "
                "WHERE agent_id = :agent_id AND credential_id = :credential_id"
            ),
            {"rule_set_id": rule_set_id, "agent_id": agent_id, "credential_id": credential_id},
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    @staticmethod
    async def count_bindings_for_rule_set(session: AsyncSession, rule_set_id: str) -> int:
        """Count direct bindings referencing a shared rule set (admin DB).

        Guards rule-set deletion: a set still pointed at by bindings must not
        vanish under them (the pointer is FK-less across the DB seam, so the
        application enforces the invariant).
        """
        result = await session.execute(
            text("SELECT COUNT(*) FROM agent_credential_bindings WHERE rule_set_id = :rule_set_id"),
            {"rule_set_id": rule_set_id},
        )
        return int(result.scalar_one())
