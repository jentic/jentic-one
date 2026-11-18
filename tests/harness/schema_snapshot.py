"""Database schema snapshot helper for migration round-trip verification.

Reflects tables, columns (name, type, nullable), indexes (name, columns/expressions,
unique), unique constraints, and foreign keys into a deterministic, comparable
structure while excluding ``alembic_version``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, pool
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.ext.asyncio import create_async_engine

from jentic_one.migrations import run as run_mod
from jentic_one.migrations.targets import DB_TARGETS
from jentic_one.shared.config import DatabaseConfig, load_config
from jentic_one.shared.db.session import get_database_url

#: Revisions whose ``downgrade()`` intentionally raises (irreversible drops).
IRREVERSIBLE: dict[str, set[str]] = {
    "admin": {run_mod.SA_DROP_REVISION},
}

TableSnapshot = dict[str, list[tuple[Any, ...]]]
SchemaSnapshot = dict[str, TableSnapshot]


def _index_columns(idx: Mapping[str, Any]) -> tuple[str, ...]:
    """Extract canonical column or expression names for a reflected index."""
    raw_cols = idx.get("column_names") or ()
    expressions = idx.get("expressions") or ()
    if raw_cols and any(col is not None for col in raw_cols):
        result: list[str] = []
        for pos, col in enumerate(raw_cols):
            if col is not None:
                result.append(str(col))
            elif pos < len(expressions):
                result.append(str(expressions[pos]))
            else:
                result.append("")
        return tuple(result)
    return tuple(str(expr) for expr in expressions)


def snapshot_connection(
    conn: Connection,
    *,
    schema: str | None = None,
    exclude_tables: Collection[str] = ("alembic_version",),
) -> SchemaSnapshot:
    """Reflect ``conn`` into a normalized per-table schema snapshot."""
    excluded = set(exclude_tables)
    inspector = inspect(conn)
    table_names = sorted(
        name for name in inspector.get_table_names(schema=schema) if name not in excluded
    )
    snapshot: SchemaSnapshot = {}
    for table_name in table_names:
        columns = sorted(
            (
                str(col["name"]),
                str(col["type"]),
                bool(col.get("nullable", True)),
            )
            for col in inspector.get_columns(table_name, schema=schema)
        )
        indexes = sorted(
            (
                str(idx.get("name") or ""),
                _index_columns(idx),
                bool(idx.get("unique", False)),
            )
            for idx in inspector.get_indexes(table_name, schema=schema)
        )
        unique_constraints = sorted(
            (
                str(uc.get("name") or ""),
                tuple(str(col) for col in (uc.get("column_names") or ())),
            )
            for uc in inspector.get_unique_constraints(table_name, schema=schema)
        )
        foreign_keys = sorted(
            (
                str(fk.get("name") or ""),
                tuple(str(col) for col in (fk.get("constrained_columns") or ())),
                str(fk.get("referred_table") or ""),
                tuple(str(col) for col in (fk.get("referred_columns") or ())),
            )
            for fk in inspector.get_foreign_keys(table_name, schema=schema)
        )
        snapshot[table_name] = {
            "columns": columns,
            "indexes": indexes,
            "unique_constraints": unique_constraints,
            "foreign_keys": foreign_keys,
        }
    return snapshot


async def _snapshot_db_config_async(
    db_config: DatabaseConfig,
    *,
    schema: str | None = None,
    exclude_tables: Collection[str] = ("alembic_version",),
) -> SchemaSnapshot:
    pg = db_config.backend == "postgres"
    target_schema = (schema if schema is not None else db_config.schema_name) if pg else None
    url = get_database_url(db_config)
    if pg:
        engine = create_async_engine(
            url,
            poolclass=pool.NullPool,
            connect_args={"server_settings": {"search_path": f"{target_schema},public"}},
        )
    else:
        engine = create_async_engine(url, poolclass=pool.NullPool)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(
                lambda sync_conn: snapshot_connection(
                    sync_conn,
                    schema=target_schema,
                    exclude_tables=exclude_tables,
                )
            )
    finally:
        await engine.dispose()


def schema_snapshot(
    target: str | Path | Engine | Connection | DatabaseConfig,
    *,
    schema: str | None = None,
    exclude_tables: Collection[str] = ("alembic_version",),
) -> SchemaSnapshot:
    """Return a normalized schema snapshot for a surface name, path, engine, or connection."""
    if isinstance(target, Connection):
        return snapshot_connection(target, schema=schema, exclude_tables=exclude_tables)
    if isinstance(target, Engine):
        with target.connect() as conn:
            return snapshot_connection(conn, schema=schema, exclude_tables=exclude_tables)
    if isinstance(target, Path):
        engine = create_engine(f"sqlite:///{target}")
        try:
            with engine.connect() as conn:
                return snapshot_connection(conn, schema=schema, exclude_tables=exclude_tables)
        finally:
            engine.dispose()
    if isinstance(target, DatabaseConfig):
        return asyncio.run(
            _snapshot_db_config_async(target, schema=schema, exclude_tables=exclude_tables)
        )
    if isinstance(target, str):
        app_config = load_config()
        db_config: DatabaseConfig = getattr(app_config.databases, target)
        excluded = set(exclude_tables)
        if target in DB_TARGETS:
            excluded.add(DB_TARGETS[target].version_table)
        return asyncio.run(
            _snapshot_db_config_async(db_config, schema=schema, exclude_tables=excluded)
        )
    raise TypeError(f"Unsupported snapshot target: {target!r}")


_schema_snapshot = schema_snapshot
