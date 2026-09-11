"""Repositories for the theme-5 Phase 6a toolkit-flattening job.

Two halves, mirroring the Phase-4 key-retirement seam
(``control/repos/key_retirement_repo.py``):

- :class:`FlatteningControlRepository` — ORM reads over the control-DB
  legacy tables (``toolkits``, ``toolkit_credential_bindings``,
  ``toolkit_permission_rules``, ``toolkit_keys``), the direct-model rule
  tables the verification compares against, and the acknowledgement
  sentinel insert.
- :class:`FlatteningAdminRepository` — raw-SQL statements against the
  **admin** DB (``agent_toolkit_bindings`` reads, actor existence checks,
  ``agent_credential_bindings`` reads/inserts, scope-grant reads). The
  control module must not import admin ORM models, so — like
  ``KeyRetirementRepository`` — every admin-side statement is raw SQL.

Timestamps crossing the raw-SQL seam are normalized by ``_as_utc``: asyncpg
returns aware datetimes, aiosqlite returns naive strings; both sides of the
job need aware-UTC values. Writes bind naive-UTC strings on SQLite (matching
SQLAlchemy's own storage format so ORM reads keep parsing) and aware
datetimes on Postgres.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.toolkit_credential_bindings import ToolkitCredentialBinding
from jentic_one.control.core.schema.toolkit_flattening_acks import ToolkitFlatteningAck
from jentic_one.control.core.schema.toolkit_keys import ToolkitKey
from jentic_one.control.core.schema.toolkit_permission_rules import ToolkitPermissionRule
from jentic_one.control.core.schema.toolkits import Toolkit
from jentic_one.shared.db.ids import generate_ksuid

#: Provenance stamp for every row the flattening job derives — mirrors the
#: Phase-4 ``system:theme5-key-retirement`` convention.
SYSTEM_ACTOR = "system:theme5-flattening"


def _as_utc(value: dt.datetime | str | None) -> dt.datetime | None:
    """Normalize a raw-SQL timestamp to an aware-UTC datetime.

    asyncpg returns aware datetimes; aiosqlite returns the naive string
    SQLAlchemy stored. Naive values are assumed UTC (the ``UTCDateTime``
    write-side convention).
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value)
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def _bind_ts(session: AsyncSession, value: dt.datetime) -> dt.datetime | str:
    """Bind parameter form of a timestamp for raw SQL on this session's dialect.

    SQLite gets the naive-UTC string format SQLAlchemy itself stores (so ORM
    reads keep parsing the column); Postgres gets the aware datetime.
    """
    if session.get_bind().dialect.name == "sqlite":
        return value.astimezone(dt.UTC).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")
    return value


class FlatteningControlRepository:
    """Control-DB reads for the flattening job — flush-only, never commits."""

    @staticmethod
    async def list_toolkits(session: AsyncSession) -> list[Toolkit]:
        result = await session.execute(select(Toolkit).order_by(Toolkit.id))
        return list(result.scalars().all())

    @staticmethod
    async def list_credential_bindings(session: AsyncSession) -> list[ToolkitCredentialBinding]:
        result = await session.execute(
            select(ToolkitCredentialBinding).order_by(ToolkitCredentialBinding.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_permission_rules(session: AsyncSession) -> list[ToolkitPermissionRule]:
        """Every legacy rule row, in per-pair evaluation order.

        Ordered ``(toolkit_id, credential_id, is_system, sequence)`` — the
        same order ``ToolkitPermissionRepository.list_rules`` serves a single
        pair in, so grouping by pair preserves evaluation order.
        """
        result = await session.execute(
            select(ToolkitPermissionRule).order_by(
                ToolkitPermissionRule.toolkit_id,
                ToolkitPermissionRule.credential_id,
                ToolkitPermissionRule.is_system.asc(),
                ToolkitPermissionRule.sequence.asc(),
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_toolkit_keys(session: AsyncSession) -> list[ToolkitKey]:
        result = await session.execute(select(ToolkitKey).order_by(ToolkitKey.id))
        return list(result.scalars().all())

    @staticmethod
    async def list_credentials(session: AsyncSession) -> list[Credential]:
        result = await session.execute(select(Credential).order_by(Credential.id))
        return list(result.scalars().all())

    @staticmethod
    async def list_inline_rules(
        session: AsyncSession, *, agent_id: str, credential_id: str
    ) -> list[AgentPermissionRule]:
        """A direct binding's inline rules, in evaluation order (verification read)."""
        result = await session.execute(
            select(AgentPermissionRule)
            .where(
                AgentPermissionRule.agent_id == agent_id,
                AgentPermissionRule.credential_id == credential_id,
            )
            .order_by(
                AgentPermissionRule.is_system.asc(),
                AgentPermissionRule.sequence.asc(),
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def record_acknowledgement(
        session: AsyncSession,
        *,
        acknowledged_at: dt.datetime,
        legacy_pair_count: int,
        direct_binding_count: int,
        report_finding_count: int,
        tool_version: str,
    ) -> ToolkitFlatteningAck:
        """Insert the Phase-6b gate row — only call after a passed verification."""
        ack = ToolkitFlatteningAck(
            acknowledged_at=acknowledged_at,
            legacy_pair_count=legacy_pair_count,
            direct_binding_count=direct_binding_count,
            report_finding_count=report_finding_count,
            tool_version=tool_version,
            created_by=SYSTEM_ACTOR,
        )
        session.add(ack)
        await session.flush()
        return ack


_LIST_AGENT_TOOLKIT_BINDINGS = text(
    "SELECT id, agent_id, toolkit_id, bound_at FROM agent_toolkit_bindings ORDER BY id"
)

_LIST_CREDENTIAL_BINDING_PAIRS = text(
    "SELECT agent_id, credential_id, rule_set_id FROM agent_credential_bindings"
)

_COUNT_CREDENTIAL_BINDINGS = text("SELECT count(*) AS n FROM agent_credential_bindings")

_LIST_AGENT_IDS = text("SELECT id FROM agents")

_LIST_SERVICE_ACCOUNT_IDS = text("SELECT id FROM service_accounts")

_LIST_SCOPES_FOR_ACTORS = text(
    "SELECT actor_id, scope FROM actor_scope_grants ORDER BY actor_id, scope"
)

_INSERT_CREDENTIAL_BINDING = text(
    "INSERT INTO agent_credential_bindings"
    " (id, agent_id, credential_id, rule_set_id, bound_at, created_by)"
    " VALUES (:id, :agent_id, :credential_id, :rule_set_id, :bound_at, :created_by)"
    " ON CONFLICT (agent_id, credential_id) DO NOTHING"
)


class FlatteningAdminRepository:
    """Admin-DB statements for the flattening job — raw SQL, flush-only."""

    @staticmethod
    async def list_agent_toolkit_bindings(
        session: AsyncSession,
    ) -> list[tuple[str, str, str, dt.datetime | None]]:
        """Every legacy actor↔toolkit binding: (id, agent_id, toolkit_id, bound_at)."""
        rows = (await session.execute(_LIST_AGENT_TOOLKIT_BINDINGS)).all()
        return [(str(r.id), str(r.agent_id), str(r.toolkit_id), _as_utc(r.bound_at)) for r in rows]

    @staticmethod
    async def list_direct_binding_pairs(
        session: AsyncSession,
    ) -> dict[tuple[str, str], str | None]:
        """Existing direct bindings: {(agent_id, credential_id): rule_set_id}."""
        rows = (await session.execute(_LIST_CREDENTIAL_BINDING_PAIRS)).all()
        return {
            (str(r.agent_id), str(r.credential_id)): (
                str(r.rule_set_id) if r.rule_set_id is not None else None
            )
            for r in rows
        }

    @staticmethod
    async def count_direct_bindings(session: AsyncSession) -> int:
        row = (await session.execute(_COUNT_CREDENTIAL_BINDINGS)).one()
        return int(row.n)

    @staticmethod
    async def list_actor_ids(session: AsyncSession) -> set[str]:
        """Every id an ``agent_toolkit_bindings.agent_id`` may legitimately hold."""
        agents = (await session.execute(_LIST_AGENT_IDS)).scalars().all()
        service_accounts = (await session.execute(_LIST_SERVICE_ACCOUNT_IDS)).scalars().all()
        return {str(i) for i in agents} | {str(i) for i in service_accounts}

    @staticmethod
    async def list_scopes_by_actor(session: AsyncSession) -> dict[str, list[str]]:
        """All scope grants grouped by actor id (converted-identity report read)."""
        rows = (await session.execute(_LIST_SCOPES_FOR_ACTORS)).all()
        scopes: dict[str, list[str]] = {}
        for row in rows:
            scopes.setdefault(str(row.actor_id), []).append(str(row.scope))
        return scopes

    @staticmethod
    async def insert_credential_binding(
        session: AsyncSession,
        *,
        agent_id: str,
        credential_id: str,
        rule_set_id: str | None,
        bound_at: dt.datetime,
    ) -> str:
        """Insert one derived direct binding; return its id.

        Idempotent via the natural unique constraint
        (``uq_agent_credential_bindings_agent_credential``) — the service
        checks pair existence first, ON CONFLICT DO NOTHING is the belt for
        a bind racing the job (double-run-and-diff catches the rules side).
        """
        binding_id = generate_ksuid("acb")
        await session.execute(
            _INSERT_CREDENTIAL_BINDING,
            {
                "id": binding_id,
                "agent_id": agent_id,
                "credential_id": credential_id,
                "rule_set_id": rule_set_id,
                "bound_at": _bind_ts(session, bound_at),
                "created_by": SYSTEM_ACTOR,
            },
        )
        return binding_id
