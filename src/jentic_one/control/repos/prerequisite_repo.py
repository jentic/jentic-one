"""Cross-database reads against admin tables for the control module.

Existence checks (access-request prerequisites) and labelling lookups (display
enrichment) share one seam: raw SQL (text()) so the control module never
imports admin ORM models.
"""

from __future__ import annotations

from datetime import datetime
from typing import NamedTuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class BoundAgentRow(NamedTuple):
    """Result row for agents bound to a toolkit."""

    binding_id: str
    agent_id: str
    agent_name: str
    agent_status: str
    agent_created_at: datetime
    bound_at: datetime


class CredentialBoundAgentRow(NamedTuple):
    """Result row for agents directly bound to a credential (theme 5 phase 1)."""

    binding_id: str
    agent_id: str
    agent_name: str
    agent_status: str
    bound_at: datetime
    suspended: bool
    rule_set_id: str | None


class AgentCredentialBindingRow(NamedTuple):
    """Direct agent↔credential binding existence-check row (theme 5 phase 1)."""

    binding_id: str
    suspended: bool
    rule_set_id: str | None


class UserDisplayRow(NamedTuple):
    """Display fields for a user, resolved cross-DB for labelling only."""

    user_id: str
    email: str
    first_name: str | None
    last_name: str | None


class PrerequisiteRepository:
    """Cross-DB reads (existence checks + labelling lookups) without admin imports."""

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
    async def get_user_displays(
        session: AsyncSession, *, user_ids: list[str]
    ) -> dict[str, UserDisplayRow]:
        """Batch-resolve user display info by id (admin DB), keyed by user id.

        Labelling only — never authorization. Ids that don't resolve (agents,
        service accounts, deleted rows) are simply absent from the result, so
        callers degrade to showing the raw id. Deactivated users ARE returned:
        a decided request whose owner was later offboarded should still show
        who owned it.
        """
        if not user_ids:
            return {}
        placeholders = ", ".join(f":uid_{i}" for i in range(len(user_ids)))
        params: dict[str, object] = {f"uid_{i}": uid for i, uid in enumerate(user_ids)}
        result = await session.execute(
            text(
                f"SELECT id, email, first_name, last_name FROM users WHERE id IN ({placeholders})"
            ),
            params,
        )
        return {row[0]: UserDisplayRow(*row) for row in result.fetchall()}

    @staticmethod
    async def agent_toolkit_binding_exists(
        session: AsyncSession, *, agent_id: str, toolkit_id: str
    ) -> bool:
        """Return True if a binding exists between the given agent and toolkit."""
        result = await session.execute(
            text(
                "SELECT 1 FROM agent_toolkit_bindings "
                "WHERE agent_id = :agent_id AND toolkit_id = :toolkit_id LIMIT 1"
            ),
            {"agent_id": agent_id, "toolkit_id": toolkit_id},
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def agent_bound_to_any_toolkit(
        session: AsyncSession, *, agent_id: str, toolkit_ids: list[str]
    ) -> bool:
        """Return True if the agent is bound to at least one of the given toolkits.

        Batched variant of :meth:`agent_toolkit_binding_exists` for
        satisfaction checks (issue #826). The current annotator only probes a
        single resolved toolkit per item (ambiguous references are left
        un-annotated), but the batched shape stays so future callers can check
        several candidates in one query. Empty ``toolkit_ids`` short-circuits
        to False.
        """
        if not toolkit_ids:
            return False
        placeholders = ", ".join(f":tk_{i}" for i in range(len(toolkit_ids)))
        params: dict[str, object] = {f"tk_{i}": tid for i, tid in enumerate(toolkit_ids)}
        params["agent_id"] = agent_id
        result = await session.execute(
            text(
                "SELECT 1 FROM agent_toolkit_bindings "
                f"WHERE agent_id = :agent_id AND toolkit_id IN ({placeholders}) LIMIT 1"
            ),
            params,
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def actor_scope_grant_exists(session: AsyncSession, *, actor_id: str, scope: str) -> bool:
        """Return True if the actor already holds this scope grant (admin DB).

        Mirrors the uniqueness key of ``EffectsRepository.grant_scope_to_actor``'s
        idempotent insert (``(actor_id, scope)``), so "exists" here is exactly
        "the grant effect would be a no-op".
        """
        result = await session.execute(
            text(
                "SELECT 1 FROM actor_scope_grants "
                "WHERE actor_id = :actor_id AND scope = :scope LIMIT 1"
            ),
            {"actor_id": actor_id, "scope": scope},
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def list_toolkit_ids_for_agent(session: AsyncSession, *, agent_id: str) -> list[str]:
        """Return the ids of every toolkit the agent is actively bound to.

        Used to widen control-surface read scoping: an agent must always be able
        to see a toolkit (and its credentials) it is bound to, even when the
        toolkit is owned by someone else — or by no one, as with the orphaned
        bootstrap agent (issues #665/#682). The binding lives in the admin DB, so
        this runs against an admin session and returns plain ids the caller feeds
        into ``build_access_filters`` (the control scoping module must not import
        admin ORM models or query across databases).
        """
        result = await session.execute(
            text("SELECT toolkit_id FROM agent_toolkit_bindings WHERE agent_id = :agent_id"),
            {"agent_id": agent_id},
        )
        return [row[0] for row in result.fetchall()]

    @staticmethod
    async def delete_agent_toolkit_bindings_for_toolkit(
        session: AsyncSession, *, toolkit_id: str
    ) -> int:
        """Delete all agent-toolkit bindings for a toolkit (cross-DB cleanup)."""
        result = await session.execute(
            text("DELETE FROM agent_toolkit_bindings WHERE toolkit_id = :toolkit_id"),
            {"toolkit_id": toolkit_id},
        )
        return int(result.rowcount)  # type: ignore[attr-defined]

    @staticmethod
    async def list_agents_for_toolkit(
        session: AsyncSession,
        *,
        toolkit_id: str,
        cursor: tuple[datetime, str] | None = None,
        limit: int = 50,
    ) -> list[BoundAgentRow]:
        """Return agents bound to a toolkit, paginated by (bound_at DESC, id DESC)."""
        if cursor is not None:
            cursor_ts, cursor_id = cursor
            result = await session.execute(
                text(
                    "SELECT b.id, a.id, a.name, a.status, a.created_at, b.bound_at "
                    "FROM agent_toolkit_bindings b "
                    "JOIN agents a ON a.id = b.agent_id "
                    "WHERE b.toolkit_id = :toolkit_id "
                    "AND (b.bound_at < :cursor_ts "
                    "     OR (b.bound_at = :cursor_ts AND b.id < :cursor_id)) "
                    "ORDER BY b.bound_at DESC, b.id DESC "
                    "LIMIT :limit"
                ),
                {
                    "toolkit_id": toolkit_id,
                    "cursor_ts": cursor_ts,
                    "cursor_id": cursor_id,
                    "limit": limit,
                },
            )
        else:
            result = await session.execute(
                text(
                    "SELECT b.id, a.id, a.name, a.status, a.created_at, b.bound_at "
                    "FROM agent_toolkit_bindings b "
                    "JOIN agents a ON a.id = b.agent_id "
                    "WHERE b.toolkit_id = :toolkit_id "
                    "ORDER BY b.bound_at DESC, b.id DESC "
                    "LIMIT :limit"
                ),
                {"toolkit_id": toolkit_id, "limit": limit},
            )
        return [BoundAgentRow(*row) for row in result.fetchall()]

    @staticmethod
    async def list_agents_for_credential(
        session: AsyncSession,
        *,
        credential_id: str,
        cursor: tuple[datetime, str] | None = None,
        limit: int = 50,
    ) -> list[CredentialBoundAgentRow]:
        """Return agents directly bound to a credential, paginated by (bound_at DESC, id DESC).

        The reverse lookup behind ``GET /credentials/{id}/agents`` (theme 5
        phase 1) — the direct-binding analogue of ``list_agents_for_toolkit``
        above, reading ``agent_credential_bindings`` instead of the toolkit
        join table. Suspended bindings are included (with their flag) so the
        credential-detail view can show a reversible cut-off, not hide it.
        """
        if cursor is not None:
            cursor_ts, cursor_id = cursor
            result = await session.execute(
                text(
                    "SELECT b.id, a.id, a.name, a.status, b.bound_at, b.suspended, b.rule_set_id "
                    "FROM agent_credential_bindings b "
                    "JOIN agents a ON a.id = b.agent_id "
                    "WHERE b.credential_id = :credential_id "
                    "AND (b.bound_at < :cursor_ts "
                    "     OR (b.bound_at = :cursor_ts AND b.id < :cursor_id)) "
                    "ORDER BY b.bound_at DESC, b.id DESC "
                    "LIMIT :limit"
                ),
                {
                    "credential_id": credential_id,
                    "cursor_ts": cursor_ts,
                    "cursor_id": cursor_id,
                    "limit": limit,
                },
            )
        else:
            result = await session.execute(
                text(
                    "SELECT b.id, a.id, a.name, a.status, b.bound_at, b.suspended, b.rule_set_id "
                    "FROM agent_credential_bindings b "
                    "JOIN agents a ON a.id = b.agent_id "
                    "WHERE b.credential_id = :credential_id "
                    "ORDER BY b.bound_at DESC, b.id DESC "
                    "LIMIT :limit"
                ),
                {"credential_id": credential_id, "limit": limit},
            )
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
        ``delete_agent_toolkit_bindings_for_toolkit`` above. ``None`` detaches:
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
