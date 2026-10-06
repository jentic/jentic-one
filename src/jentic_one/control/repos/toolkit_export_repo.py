"""Repository for the theme-5 Phase 6a toolkit export/import job (P-01).

**Migration-independent by design**: this job's whole purpose is the Phase-6b
rollback drill — it must run on post-drop code (whose head schema declares no
toolkit ORM models) against a downgraded database whose tables were just
recreated empty. Every statement is therefore raw SQL built from the
service's fixed schema_version-1 column tuples; nothing here imports a
toolkit model.

Import is idempotent by primary key: rows whose id already exists are
skipped, so a re-run after a partial failure (control committed, admin
crashed) completes the missing side without duplicating the other. Exported
rows carry their original ids, so no KSUID defaults are needed.

Dialect notes: timestamps bind through ``_bind_ts`` (naive-UTC strings on
SQLite, aware datetimes on Postgres); JSON-array columns bind as serialized
text, cast to JSONB on Postgres; booleans bind natively (SQLite stores 0/1,
which the read side coerces back).
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.control.repos.toolkit_flattening_repo import _bind_ts

#: Columns holding JSON arrays — serialized on write, parsed on read.
JSON_COLUMNS = frozenset({"allowed_ips", "methods", "operations"})

#: Columns holding booleans — SQLite returns 0/1 from raw SQL.
BOOLEAN_COLUMNS = frozenset({"active", "revoked", "is_system"})

_TIMESTAMP_COLUMNS = frozenset({"created_at", "updated_at", "bound_at", "last_used_at"})

#: The five legacy tables this job may touch — the only values accepted for
#: the ``table`` parameters below (defense-in-depth for the f-string SQL).
_KNOWN_TABLES = frozenset(
    {
        "toolkits",
        "toolkit_keys",
        "toolkit_credential_bindings",
        "toolkit_permission_rules",
        "agent_toolkit_bindings",
    }
)


def _check_table(table: str) -> str:
    if table not in _KNOWN_TABLES:
        raise ValueError(f"unknown export table {table!r}")
    return table


def _bind_value(session: AsyncSession, column: str, value: Any) -> Any:
    if column in JSON_COLUMNS:
        return json.dumps(value) if value is not None else None
    if column in _TIMESTAMP_COLUMNS and isinstance(value, dt.datetime):
        return _bind_ts(session, value)
    return value


class ExportRepository:
    """Raw-SQL export/import statements — flush-only, never commits.

    One class serves both databases: the service passes it control sessions
    for the four control tables and admin sessions for
    ``agent_toolkit_bindings``; the SQL itself is identical in shape.
    """

    @staticmethod
    async def list_rows(session: AsyncSession, table: str, columns: Sequence[str]) -> list[Any]:
        """Every row of one table, raw (the service owns serialization)."""
        query = text(f"SELECT {', '.join(columns)} FROM {_check_table(table)} ORDER BY id")
        return list((await session.execute(query)).all())

    @staticmethod
    async def existing_ids(session: AsyncSession, table: str) -> set[str]:
        query = text(f"SELECT id FROM {_check_table(table)}")
        result = await session.execute(query)
        return {str(i) for i in result.scalars().all()}

    @staticmethod
    async def insert_rows(
        session: AsyncSession, table: str, columns: Sequence[str], rows: list[dict[str, Any]]
    ) -> None:
        """Insert exported rows verbatim (ids, timestamps, and all)."""
        if not rows:
            return
        pg = session.get_bind().dialect.name == "postgresql"
        placeholders = ", ".join(
            f"CAST(:{column} AS JSONB)" if pg and column in JSON_COLUMNS else f":{column}"
            for column in columns
        )
        stmt = text(
            f"INSERT INTO {_check_table(table)} ({', '.join(columns)}) VALUES ({placeholders})"
        )
        for row in rows:
            await session.execute(
                stmt,
                {column: _bind_value(session, column, row.get(column)) for column in columns},
            )
