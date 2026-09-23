"""Pin the ``actor_scope_grants`` → ``actor_permission_grants`` rename on both dialects.

Migration ``d1e2f3a4b5c6`` is a pure rename, but two parts of it are easy to get
silently wrong and invisible to the head-schema tests (which only ever see the
end state of a fresh database):

- existing rows must survive with their values and ``asg_`` ids intact;
- on SQLite the column rename and the unique-constraint swap need separate
  batch rebuilds, or the rebuild drops the constraint that the
  ``ON CONFLICT (actor_id, permission)`` upsert in
  ``EffectsRepository.grant_permission_to_actor`` depends on.

Each test migrates a throwaway database (a fresh Postgres schema, or a fresh
SQLite file) to the revision just before the rename, seeds rows the way the old
code wrote them, then walks the rename up, down, and up again.

Sync on purpose: the migration env calls ``asyncio.run`` itself, so
``command.upgrade`` cannot run inside an async test's event loop.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from pydantic import SecretStr
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from jentic_one.shared.config import AppConfig, DatabaseConfig
from jentic_one.shared.db.session import get_database_url

from ..conftest import _alembic_config_for, _test_backend

pytestmark = pytest.mark.integration

_BEFORE = "c0d1e2f3a4b5"  # pragma: allowlist secret
_RENAME = "d1e2f3a4b5c6"  # pragma: allowlist secret

_OLD_TABLE = "actor_scope_grants"
_NEW_TABLE = "actor_permission_grants"

_SEED = (
    ("asg_rename_test_000000000001", "agnt_rename_1", "agent", "agents:write"),
    ("asg_rename_test_000000000002", "agnt_rename_1", "agent", "capabilities:execute"),
    ("asg_rename_test_000000000003", "sa_rename_1", "service_account", "capabilities:execute"),
)


class _Target:
    """A throwaway admin database plus the handles needed to migrate and inspect it."""

    def __init__(self, db_config: DatabaseConfig, *, postgres: bool) -> None:
        self.db_config = db_config
        self.postgres = postgres
        self.schema: str | None = db_config.schema_name if postgres else None
        self.alembic: AlembicConfig = _alembic_config_for("admin", db_config)

    def engine(self) -> AsyncEngine:
        return create_async_engine(get_database_url(self.db_config))

    def qualified(self, table: str) -> str:
        return f'"{self.schema}".{table}' if self.schema else table


@pytest.fixture()
def admin_target(integration_config: AppConfig, tmp_path: Path) -> Iterator[_Target]:
    base = integration_config.databases.admin
    if _test_backend() == "postgres":
        schema = f"asg_rename_{uuid.uuid4().hex[:8]}"
        # Superuser: the migration env creates the schema, which needs CREATE on
        # the database (the harness's per-surface users deliberately lack it).
        cfg = base.model_copy(
            update={"schema_name": schema, "user": "postgres", "password": SecretStr("postgres")}
        )
        target = _Target(cfg, postgres=True)
        try:
            yield target
        finally:

            async def _drop() -> None:
                engine = target.engine()
                try:
                    async with engine.begin() as conn:
                        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
                finally:
                    await engine.dispose()

            asyncio.run(_drop())
    else:
        cfg = base.model_copy(update={"path": str(tmp_path / "admin.db")})
        yield _Target(cfg, postgres=False)


def _run(target: _Target, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
    async def _go() -> list[Any]:
        engine = target.engine()
        try:
            async with engine.begin() as conn:
                result = await conn.execute(text(sql), params or {})
                return list(result.all()) if result.returns_rows else []
        finally:
            await engine.dispose()

    return asyncio.run(_go())


def _shape(target: _Target, table: str) -> dict[str, Any]:
    """Reflect the parts of the table the rename touches."""

    async def _go() -> dict[str, Any]:
        engine = target.engine()
        try:
            async with engine.connect() as conn:

                def _reflect(sync_conn: Any) -> dict[str, Any]:
                    insp = inspect(sync_conn)
                    return {
                        "tables": set(insp.get_table_names(schema=target.schema)),
                        "columns": {c["name"] for c in insp.get_columns(table, target.schema)},
                        "uniques": {
                            (u["name"], tuple(u["column_names"]))
                            for u in insp.get_unique_constraints(table, target.schema)
                        },
                        "indexes": {
                            i["name"]
                            for i in insp.get_indexes(table, target.schema)
                            if i["name"] is not None
                        },
                        "pk": insp.get_pk_constraint(table, target.schema).get("name"),
                    }

                return await conn.run_sync(_reflect)
        finally:
            await engine.dispose()

    return asyncio.run(_go())


def _seed_old(target: _Target) -> None:
    for row_id, actor_id, actor_type, scope in _SEED:
        _run(
            target,
            f"INSERT INTO {target.qualified(_OLD_TABLE)} "
            "(id, actor_id, actor_type, scope, granted_by, created_by) "
            "VALUES (:id, :actor_id, :actor_type, :scope, NULL, NULL)",
            {"id": row_id, "actor_id": actor_id, "actor_type": actor_type, "scope": scope},
        )


def _rows(target: _Target, table: str, column: str) -> set[tuple[str, str, str, str]]:
    return {
        (r[0], r[1], r[2], r[3])
        for r in _run(
            target,
            f"SELECT id, actor_id, actor_type, {column} FROM {target.qualified(table)}",
        )
    }


def _assert_new_shape(target: _Target) -> None:
    shape = _shape(target, _NEW_TABLE)
    assert _NEW_TABLE in shape["tables"]
    assert _OLD_TABLE not in shape["tables"]
    assert "permission" in shape["columns"]
    assert "scope" not in shape["columns"]
    assert (
        "uq_actor_permission_grants_actor_permission",
        ("actor_id", "permission"),
    ) in shape["uniques"]
    assert {
        "ix_actor_permission_grants_permission",
        "ix_actor_permission_grants_actor",
        "ix_actor_permission_grants_created_at",
        "ix_actor_permission_grants_created_by",
    } <= shape["indexes"]
    assert not any("scope" in name for name in shape["indexes"])
    if target.postgres:
        assert shape["pk"] == "actor_permission_grants_pkey"


def _assert_upsert_is_idempotent(target: _Target) -> None:
    """The grant path's ``ON CONFLICT (actor_id, permission)`` needs the unique constraint."""
    for _ in range(2):
        _run(
            target,
            f"INSERT INTO {target.qualified(_NEW_TABLE)} "
            "(id, actor_id, actor_type, permission, granted_by, created_by) "
            "VALUES (:id, 'agnt_rename_1', 'agent', 'agents:write', NULL, NULL) "
            "ON CONFLICT (actor_id, permission) DO NOTHING",
            {"id": f"asg_rename_upsert_{uuid.uuid4().hex[:12]}"},
        )
    count = _run(
        target,
        f"SELECT COUNT(*) FROM {target.qualified(_NEW_TABLE)} "
        "WHERE actor_id = 'agnt_rename_1' AND permission = 'agents:write'",
    )
    assert count[0][0] == 1


def test_rename_preserves_rows_and_the_upsert_constraint(admin_target: _Target) -> None:
    command.upgrade(admin_target.alembic, _BEFORE)
    _seed_old(admin_target)

    command.upgrade(admin_target.alembic, _RENAME)

    _assert_new_shape(admin_target)
    assert _rows(admin_target, _NEW_TABLE, "permission") == set(_SEED)
    _assert_upsert_is_idempotent(admin_target)
    with pytest.raises(IntegrityError):
        _run(
            admin_target,
            f"INSERT INTO {admin_target.qualified(_NEW_TABLE)} "
            "(id, actor_id, actor_type, permission, granted_by, created_by) "
            "VALUES ('asg_rename_test_dup', 'agnt_rename_1', 'agent', 'agents:write', NULL, NULL)",
        )


def test_rename_round_trips(admin_target: _Target) -> None:
    command.upgrade(admin_target.alembic, _BEFORE)
    _seed_old(admin_target)
    command.upgrade(admin_target.alembic, _RENAME)

    command.downgrade(admin_target.alembic, _BEFORE)

    old = _shape(admin_target, _OLD_TABLE)
    assert _OLD_TABLE in old["tables"]
    assert _NEW_TABLE not in old["tables"]
    assert "scope" in old["columns"]
    assert ("uq_actor_scope_grants_actor_scope", ("actor_id", "scope")) in old["uniques"]
    assert {
        "ix_actor_scope_grants_scope",
        "ix_actor_scope_grants_actor",
        "ix_actor_scope_grants_created_at",
        "ix_actor_scope_grants_created_by",
    } <= old["indexes"]
    if admin_target.postgres:
        assert old["pk"] == "actor_scope_grants_pkey"
    assert _rows(admin_target, _OLD_TABLE, "scope") == set(_SEED)

    command.upgrade(admin_target.alembic, _RENAME)

    _assert_new_shape(admin_target)
    assert _rows(admin_target, _NEW_TABLE, "permission") == set(_SEED)
    _assert_upsert_is_idempotent(admin_target)
