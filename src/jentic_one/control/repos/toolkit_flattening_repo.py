"""Repositories for the theme-5 Phase 6a toolkit-flattening job.

Two halves, mirroring the (since-deleted) Phase-4 key-retirement seam:

- :class:`FlatteningControlRepository` — control-DB reads over the legacy
  toolkit tables, the direct-model rule tables the verification compares
  against, and the acknowledgement sentinel insert.
- :class:`FlatteningAdminRepository` — raw-SQL statements against the
  **admin** DB (``agent_toolkit_bindings`` reads, actor existence checks,
  ``agent_credential_bindings`` reads/inserts, scope-grant reads, and the
  ``execution_records.toolkit_name`` backfill). The control module must not
  import admin ORM models, so every
  admin-side statement is raw SQL.

The legacy-table reads (``toolkits``, ``toolkit_credential_bindings``,
``toolkit_permission_rules``, ``toolkit_keys``) are **migration-independent
raw SQL** into typed row dataclasses: the Phase-6b drop migrations delete
those tables' ORM models, but this job still runs on post-6b code against a
pre-drop (or downgraded-and-reimported) database, so it must not depend on
models the head schema no longer declares.

Timestamps crossing the raw-SQL seam are normalized by ``_as_utc``: asyncpg
returns aware datetimes, aiosqlite returns naive strings; both sides of the
job need aware-UTC values. JSON columns are normalized by ``_as_json_list``
(Postgres decodes JSONB to lists; SQLite returns the stored TEXT). Writes
bind naive-UTC strings on SQLite (matching SQLAlchemy's own storage format
so ORM reads keep parsing) and aware datetimes on Postgres.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.toolkit_flattening_acks import ToolkitFlatteningAck
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


def _as_json_list(value: object) -> list[object] | None:
    """Normalize a raw-SQL JSON-array column to a list (both dialects).

    Postgres (asyncpg + SQLAlchemy codecs) decodes JSONB to a list; SQLite
    returns the stored TEXT.
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        raise TypeError(f"expected JSON array, got {type(value).__name__}")
    return value


@dataclass(frozen=True)
class ToolkitRow:
    """Raw-SQL projection of a legacy ``toolkits`` row (post-ORM-deletion)."""

    id: str
    name: str
    active: bool


@dataclass(frozen=True)
class ToolkitCredentialBindingRow:
    """Raw-SQL projection of a legacy ``toolkit_credential_bindings`` row."""

    id: str
    toolkit_id: str
    credential_id: str
    bound_at: dt.datetime | None


@dataclass(frozen=True)
class ToolkitPermissionRuleRow:
    """Raw-SQL projection of a legacy ``toolkit_permission_rules`` row."""

    id: str
    toolkit_id: str
    credential_id: str
    effect: str
    methods: list[object] | None
    path: str | None
    match_mode: str
    operations: list[object] | None
    is_system: bool
    comment: str | None
    sequence: int
    created_at: dt.datetime | None
    created_by: str | None


@dataclass(frozen=True)
class ToolkitKeyRow:
    """Raw-SQL projection of a legacy ``toolkit_keys`` row."""

    id: str
    toolkit_id: str
    revoked: bool
    migrated_actor_id: str | None
    label: str | None
    key_preview: str
    last_used_at: dt.datetime | None
    created_at: dt.datetime | None


_LIST_TOOLKITS = text("SELECT id, name, active FROM toolkits ORDER BY id")

_LIST_TOOLKIT_CREDENTIAL_BINDINGS = text(
    "SELECT id, toolkit_id, credential_id, bound_at FROM toolkit_credential_bindings ORDER BY id"
)

#: Per-pair evaluation order — the same order the legacy
#: ``ToolkitPermissionRepository.list_rules`` served a single pair in, so
#: grouping by pair preserves evaluation order.
_LIST_TOOLKIT_PERMISSION_RULES = text(
    "SELECT id, toolkit_id, credential_id, effect, methods, path, match_mode,"
    " operations, is_system, comment, sequence, created_at, created_by"
    " FROM toolkit_permission_rules"
    " ORDER BY toolkit_id, credential_id, is_system ASC, sequence ASC"
)

_LIST_TOOLKIT_KEYS = text(
    "SELECT id, toolkit_id, revoked, migrated_actor_id, label, key_preview,"
    " last_used_at, created_at"
    " FROM toolkit_keys ORDER BY id"
)


class FlatteningControlRepository:
    """Control-DB reads for the flattening job — flush-only, never commits.

    The legacy-table reads are raw SQL (see module docstring): they must
    work on post-6b code, whose head schema no longer declares these tables.
    """

    @staticmethod
    async def list_toolkits(session: AsyncSession) -> list[ToolkitRow]:
        result = await session.execute(_LIST_TOOLKITS)
        return [
            ToolkitRow(id=row.id, name=row.name, active=bool(row.active)) for row in result.all()
        ]

    @staticmethod
    async def list_credential_bindings(
        session: AsyncSession,
    ) -> list[ToolkitCredentialBindingRow]:
        result = await session.execute(_LIST_TOOLKIT_CREDENTIAL_BINDINGS)
        return [
            ToolkitCredentialBindingRow(
                id=row.id,
                toolkit_id=row.toolkit_id,
                credential_id=row.credential_id,
                bound_at=_as_utc(row.bound_at),
            )
            for row in result.all()
        ]

    @staticmethod
    async def list_permission_rules(session: AsyncSession) -> list[ToolkitPermissionRuleRow]:
        """Every legacy rule row, in per-pair evaluation order."""
        result = await session.execute(_LIST_TOOLKIT_PERMISSION_RULES)
        return [
            ToolkitPermissionRuleRow(
                id=row.id,
                toolkit_id=row.toolkit_id,
                credential_id=row.credential_id,
                effect=row.effect,
                methods=_as_json_list(row.methods),
                path=row.path,
                match_mode=row.match_mode,
                operations=_as_json_list(row.operations),
                is_system=bool(row.is_system),
                comment=row.comment,
                sequence=row.sequence,
                created_at=_as_utc(row.created_at),
                created_by=row.created_by,
            )
            for row in result.all()
        ]

    @staticmethod
    async def list_toolkit_keys(session: AsyncSession) -> list[ToolkitKeyRow]:
        result = await session.execute(_LIST_TOOLKIT_KEYS)
        return [
            ToolkitKeyRow(
                id=row.id,
                toolkit_id=row.toolkit_id,
                revoked=bool(row.revoked),
                migrated_actor_id=row.migrated_actor_id,
                label=row.label,
                key_preview=row.key_preview,
                last_used_at=_as_utc(row.last_used_at),
                created_at=_as_utc(row.created_at),
            )
            for row in result.all()
        ]

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

_SQLITE_TOOLKIT_NAME_COLUMN_EXISTS = text(
    "SELECT count(*) FROM pragma_table_info('execution_records') WHERE name = 'toolkit_name'"
)

#: ``current_schema()`` resolves through the connection search_path, which the
#: postgres backend pins to the admin schema — same-named tables in other
#: schemas cannot satisfy the probe.
_PG_TOOLKIT_NAME_COLUMN_EXISTS = text(
    "SELECT count(*) FROM information_schema.columns"
    " WHERE table_schema = current_schema()"
    "   AND table_name = 'execution_records' AND column_name = 'toolkit_name'"
)

_ADD_TOOLKIT_NAME_COLUMN = text(
    "ALTER TABLE execution_records ADD COLUMN toolkit_name VARCHAR(255)"
)

_LIST_UNBACKFILLED_TOOLKIT_IDS = text(
    "SELECT DISTINCT toolkit_id FROM execution_records"
    " WHERE toolkit_id IS NOT NULL AND toolkit_name IS NULL"
)

#: Fallback when the ``toolkit_name`` column does not exist yet (verify runs
#: read-only, so it cannot add the column): every historical toolkit-path
#: execution row counts as unbackfilled.
_LIST_ALL_EXECUTION_TOOLKIT_IDS = text(
    "SELECT DISTINCT toolkit_id FROM execution_records WHERE toolkit_id IS NOT NULL"
)

_BACKFILL_TOOLKIT_NAME = text(
    "UPDATE execution_records SET toolkit_name = :name"
    " WHERE toolkit_id = :toolkit_id AND toolkit_name IS NULL"
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

    @staticmethod
    async def _toolkit_name_column_exists(session: AsyncSession) -> bool:
        probe = (
            _SQLITE_TOOLKIT_NAME_COLUMN_EXISTS
            if session.get_bind().dialect.name == "sqlite"
            else _PG_TOOLKIT_NAME_COLUMN_EXISTS
        )
        return bool(int((await session.execute(probe)).scalar_one()))

    @staticmethod
    async def ensure_execution_toolkit_name_column(session: AsyncSession) -> None:
        """Add ``execution_records.toolkit_name`` if the migration hasn't yet.

        The documented runbook order is flatten-then-migrate, so the job runs
        before the admin migration (``c0e1f2a3b4c5``) that also adds this
        column; whichever runs first wins, both create the identical shape.
        Idempotent via an explicit existence probe (neither dialect supports
        ``ADD COLUMN IF NOT EXISTS`` portably).
        """
        if not await FlatteningAdminRepository._toolkit_name_column_exists(session):
            await session.execute(_ADD_TOOLKIT_NAME_COLUMN)

    @staticmethod
    async def list_unbackfilled_toolkit_ids(session: AsyncSession) -> list[str]:
        """Distinct toolkit ids on execution rows still missing the denormalized name.

        Works before the ``toolkit_name`` column exists (read-only ``verify``
        cannot add it): with no column, every toolkit-path row is unbackfilled.
        """
        if await FlatteningAdminRepository._toolkit_name_column_exists(session):
            query = _LIST_UNBACKFILLED_TOOLKIT_IDS
        else:
            query = _LIST_ALL_EXECUTION_TOOLKIT_IDS
        rows = (await session.execute(query)).scalars().all()
        return [str(toolkit_id) for toolkit_id in rows]

    @staticmethod
    async def backfill_execution_toolkit_name(
        session: AsyncSession, *, toolkit_id: str, name: str
    ) -> int:
        """Stamp one toolkit's name onto its unnamed execution rows; return the count.

        Batched per distinct toolkit id (one UPDATE per toolkit, however many
        execution rows it touches). Rows whose toolkit no longer exists in
        control are never passed here — they keep NULL, exactly as the old
        read-time resolver reported them.
        """
        result = await session.execute(
            _BACKFILL_TOOLKIT_NAME, {"toolkit_id": toolkit_id, "name": name}
        )
        rowcount = getattr(result, "rowcount", 0)  # CursorResult on DML
        return int(rowcount or 0)
