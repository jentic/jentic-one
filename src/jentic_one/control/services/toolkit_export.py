"""Theme-5 Phase 6a — verified toolkit export/import (P-01).

Exports all five legacy toolkit tables — ``toolkits``, ``toolkit_keys``,
``toolkit_credential_bindings``, ``toolkit_permission_rules`` (control DB)
and ``agent_toolkit_bindings`` (admin DB) — to one self-describing JSON
document the **same job re-imports** (``export-toolkits --import``). This is
the only row-restoring rollback path once Phase 6b drops the tables: a
migration ``downgrade()`` recreates empty schemas, and a rules-empty restore
is a total default-deny authorization outage, so the runbook is *downgrade
the drops, then re-import this file*.

Format (schema_version 1): dialect-neutral JSON — KSUID strings, ISO-8601
timestamps (aware UTC), booleans, JSON lists, nulls — with per-table row
counts so a truncated file fails validation instead of silently restoring a
subset. The file embeds key hash digests (``hashed_key``, ``lookup_hash``);
treat it like a secrets backup.

Import is idempotent by primary key (rows whose id exists are skipped), so a
re-run after a partial failure — control committed, admin crashed — completes
the missing side without duplicating the other.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

import structlog

from jentic_one import __version__
from jentic_one.control.repos.toolkit_export_repo import (
    ExportAdminRepository,
    ExportControlRepository,
)
from jentic_one.control.repos.toolkit_flattening_repo import FlatteningControlRepository
from jentic_one.shared.context import Context

logger = structlog.get_logger(__name__)

EXPORT_FORMAT = "jentic-one-toolkit-export"
SCHEMA_VERSION = 1

#: Exported columns per table, fixed per schema_version — a column added
#: later bumps the version rather than silently changing the file shape.
_COLUMNS: dict[str, tuple[str, ...]] = {
    "toolkits": ("id", "name", "description", "active", "created_at", "updated_at", "created_by"),
    "toolkit_keys": (
        "id",
        "toolkit_id",
        "label",
        "allowed_ips",
        "revoked",
        "key_preview",
        "hashed_key",
        "lookup_hash",
        "last_used_at",
        "migrated_actor_id",
        "created_at",
        "updated_at",
        "created_by",
    ),
    "toolkit_credential_bindings": (
        "id",
        "toolkit_id",
        "credential_id",
        "bound_at",
        "created_at",
        "updated_at",
        "created_by",
    ),
    "toolkit_permission_rules": (
        "id",
        "toolkit_id",
        "credential_id",
        "effect",
        "methods",
        "path",
        "match_mode",
        "operations",
        "is_system",
        "comment",
        "sequence",
        "created_at",
        "updated_at",
        "created_by",
    ),
    "agent_toolkit_bindings": (
        "id",
        "agent_id",
        "toolkit_id",
        "bound_at",
        "created_at",
        "updated_at",
        "created_by",
    ),
}

_DATETIME_COLUMNS = frozenset({"created_at", "updated_at", "bound_at", "last_used_at"})

#: Control-table import order — parents before FK children.
_CONTROL_TABLE_ORDER = (
    "toolkits",
    "toolkit_keys",
    "toolkit_credential_bindings",
    "toolkit_permission_rules",
)


def _serialize(table: str, row: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for column in _COLUMNS[table]:
        value = getattr(row, column)
        if isinstance(value, dt.datetime):
            if value.tzinfo is None:  # raw-SQL SQLite reads come back naive UTC
                value = value.replace(tzinfo=dt.UTC)
            value = value.astimezone(dt.UTC).isoformat()
        elif isinstance(value, str) and column in _DATETIME_COLUMNS:
            parsed = dt.datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.UTC)
            value = parsed.astimezone(dt.UTC).isoformat()
        out[column] = value
    return out


def _deserialize(table: str, row: dict[str, Any]) -> dict[str, Any]:
    unexpected = set(row) - set(_COLUMNS[table])
    if unexpected:
        raise ToolkitExportError(f"{table}: unexpected columns {sorted(unexpected)}")
    out: dict[str, Any] = {}
    for column in _COLUMNS[table]:
        value = row.get(column)
        if column in _DATETIME_COLUMNS and isinstance(value, str):
            parsed = dt.datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.UTC)
            value = parsed.astimezone(dt.UTC)
        out[column] = value
    return out


class ToolkitExportError(ValueError):
    """A malformed or version-mismatched export document."""


@dataclass
class ImportOutcome:
    """Per-table row counts of one import run."""

    inserted: dict[str, int] = field(default_factory=dict)
    skipped_existing: dict[str, int] = field(default_factory=dict)


class ToolkitExportService:
    """Exports and re-imports the five doomed toolkit tables."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def export(self) -> dict[str, Any]:
        """Read both databases and return the schema-versioned document."""
        async with self._ctx.control_db.session() as session:
            control_rows: dict[str, list[Any]] = {
                "toolkits": await FlatteningControlRepository.list_toolkits(session),
                "toolkit_keys": await FlatteningControlRepository.list_toolkit_keys(session),
                "toolkit_credential_bindings": (
                    await FlatteningControlRepository.list_credential_bindings(session)
                ),
                "toolkit_permission_rules": (
                    await FlatteningControlRepository.list_permission_rules(session)
                ),
            }
        async with self._ctx.admin_db.session() as session:
            admin_rows = await ExportAdminRepository.list_rows(session)

        tables: dict[str, dict[str, Any]] = {}
        for table, rows in {**control_rows, "agent_toolkit_bindings": admin_rows}.items():
            serialized = [_serialize(table, row) for row in rows]
            tables[table] = {"row_count": len(serialized), "rows": serialized}

        logger.info(
            "toolkit_export",
            **{table: body["row_count"] for table, body in tables.items()},
        )
        return {
            "format": EXPORT_FORMAT,
            "schema_version": SCHEMA_VERSION,
            "exported_at": dt.datetime.now(dt.UTC).isoformat(),
            "tool_version": __version__,
            "tables": tables,
        }

    async def import_document(self, document: dict[str, Any]) -> ImportOutcome:
        """Re-insert an exported document's rows (idempotent by primary key)."""
        self._validate(document)
        tables = document["tables"]
        outcome = ImportOutcome()

        async with self._ctx.control_db.transaction() as session:
            for table in _CONTROL_TABLE_ORDER:
                rows = [_deserialize(table, row) for row in tables[table]["rows"]]
                existing = await ExportControlRepository.existing_ids(session, table)
                missing = [row for row in rows if row["id"] not in existing]
                await ExportControlRepository.insert_rows(session, table, missing)
                outcome.inserted[table] = len(missing)
                outcome.skipped_existing[table] = len(rows) - len(missing)

        async with self._ctx.admin_db.transaction() as session:
            table = "agent_toolkit_bindings"
            rows = [_deserialize(table, row) for row in tables[table]["rows"]]
            existing = await ExportAdminRepository.existing_ids(session)
            inserted = 0
            for row in rows:
                if row["id"] in existing:
                    continue
                await ExportAdminRepository.insert_row(session, **row)
                inserted += 1
            outcome.inserted[table] = inserted
            outcome.skipped_existing[table] = len(rows) - inserted

        logger.info("toolkit_import", inserted=outcome.inserted, skipped=outcome.skipped_existing)
        return outcome

    @staticmethod
    def _validate(document: dict[str, Any]) -> None:
        if document.get("format") != EXPORT_FORMAT:
            raise ToolkitExportError(
                f"not a {EXPORT_FORMAT} document (format={document.get('format')!r})"
            )
        if document.get("schema_version") != SCHEMA_VERSION:
            raise ToolkitExportError(
                f"unsupported schema_version {document.get('schema_version')!r}; "
                f"this build reads version {SCHEMA_VERSION}"
            )
        tables = document.get("tables")
        if not isinstance(tables, dict):
            raise ToolkitExportError("missing tables object")
        for table in _COLUMNS:
            body = tables.get(table)
            if not isinstance(body, dict) or not isinstance(body.get("rows"), list):
                raise ToolkitExportError(f"missing table {table!r}")
            if body.get("row_count") != len(body["rows"]):
                raise ToolkitExportError(
                    f"{table}: row_count {body.get('row_count')!r} does not match "
                    f"{len(body['rows'])} rows — truncated or edited file"
                )
