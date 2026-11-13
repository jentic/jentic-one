"""Integration tests for ``migrations.run --direction down`` on disposable databases (#1304).

Exercises multi-DB and single-DB downgrade paths against disposable PostgreSQL
schemas (created per test and dropped with ``CASCADE`` in teardown) and
disposable ``tmp_path`` SQLite databases — never mutating shared harness schemas.
"""

from __future__ import annotations

import asyncio
import sqlite3
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from alembic.script import ScriptDirectory
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from jentic_one.migrations import run as run_mod
from jentic_one.shared.config import AppConfig
from jentic_one.shared.db.session import get_database_url
from tests.harness.postgres import encryption_config

from .conftest import _test_backend

pytestmark = pytest.mark.integration

_SURFACES = ("registry", "control", "admin")


async def _drop_pg_schemas(superuser_url: Any, schemas: dict[str, str]) -> None:
    engine = create_async_engine(superuser_url)
    try:
        async with engine.begin() as conn:
            for schema in schemas.values():
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    finally:
        await engine.dispose()


async def _pg_current_rev(superuser_url: Any, schema: str) -> str | None:
    engine = create_async_engine(superuser_url)
    try:
        async with engine.connect() as conn:
            has_table = (
                await conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema = :s AND table_name = 'alembic_version'"
                    ),
                    {"s": schema},
                )
            ).first()
            if has_table is None:
                return None
            row = (
                await conn.execute(text(f'SELECT version_num FROM "{schema}".alembic_version'))
            ).first()
            return str(row[0]) if row is not None else None
    finally:
        await engine.dispose()


def _sqlite_current_rev(db_file: Path) -> str | None:
    if not db_file.exists():
        return None
    with sqlite3.connect(db_file) as conn:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'"
        ).fetchone()
        if has_table is None:
            return None
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        return str(row[0]) if row is not None else None


@pytest.fixture()
def disposable_migration_stack(
    integration_config: AppConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Any]:
    """Configure ``JENTIC_CONFIG_FILE`` against fresh disposable schemas/files per test."""
    backend = _test_backend()
    cfg_path = tmp_path / "disposable-jentic-one.yaml"

    if backend == "postgres":
        tag = uuid.uuid4().hex[:8]
        schemas = {surface: f"dg1304_{surface}_{tag}" for surface in _SURFACES}
        base_pg = integration_config.databases.registry
        superuser_cfg = base_pg.model_copy(
            update={"user": "postgres", "password": SecretStr("postgres")}
        )
        superuser_url = get_database_url(superuser_cfg)
        raw_cfg: dict[str, Any] = {
            "databases": {
                surface: {
                    "backend": "postgres",
                    "host": base_pg.host,
                    "port": base_pg.port,
                    "name": base_pg.name,
                    "user": "postgres",
                    "password": "postgres",  # pragma: allowlist secret
                    "schema_name": schemas[surface],
                }
                for surface in _SURFACES
            },
            "credentials": encryption_config(),
        }
        cfg_path.write_text(yaml.safe_dump(raw_cfg), encoding="utf-8")
        monkeypatch.setenv("JENTIC_CONFIG_FILE", str(cfg_path))
        try:
            yield lambda surface: asyncio.run(_pg_current_rev(superuser_url, schemas[surface]))
        finally:
            asyncio.run(_drop_pg_schemas(superuser_url, schemas))
    else:
        files = {surface: tmp_path / f"{surface}.db" for surface in _SURFACES}
        raw_cfg = {
            "databases": {
                surface: {
                    "backend": "sqlite",
                    "path": str(files[surface]),
                    "schema_name": surface,
                }
                for surface in _SURFACES
            },
            "credentials": encryption_config(),
        }
        cfg_path.write_text(yaml.safe_dump(raw_cfg), encoding="utf-8")
        monkeypatch.setenv("JENTIC_CONFIG_FILE", str(cfg_path))
        yield lambda surface: _sqlite_current_rev(files[surface])


def _head_and_ancestors(db_name: str, depth: int = 3) -> list[str]:
    script = ScriptDirectory.from_config(run_mod._build_config(db_name))
    (head,) = script.get_heads()
    chain = [head]
    cur: str | None = head
    for _ in range(depth - 1):
        assert cur is not None
        rev = script.get_revision(cur)
        assert rev is not None and isinstance(rev.down_revision, str)
        cur = rev.down_revision
        chain.append(cur)
    return chain


def test_disposable_stack_refuses_ambiguous_downgrades_without_mutating_any_schema(
    disposable_migration_stack: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ambiguous multi-DB or targetless downgrades refuse before touching any disposable DB."""
    current_rev = disposable_migration_stack
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    heads = {surface: current_rev(surface) for surface in _SURFACES}
    _, admin_parent, _ = _head_and_ancestors("admin")
    capsys.readouterr()

    for argv in (
        ["--direction", "down"],
        ["--direction", "down", "--target", "-1"],
        ["--db", "control", "--direction", "down"],
        ["--db", "control", "--db", "admin", "--direction", "down", "--target", admin_parent],
    ):
        code = run_mod.main(argv)
        captured = capsys.readouterr()
        assert code != 0, f"expected refusal for {argv!r}"
        assert captured.err.strip() != ""
        for surface in _SURFACES:
            assert current_rev(surface) == heads[surface], (
                f"{surface} was mutated by refused argv {argv!r}"
            )


def test_disposable_stack_partial_downgrade_exits_nonzero_and_reports_explicit_state(
    disposable_migration_stack: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """Multi-DB ``--target base`` hitting ``admin``'s irreversible drop exits
    non-zero with explicit ``DOWNGRADE_STATUS`` lines."""
    current_rev = disposable_migration_stack
    assert run_mod.main(["--skip-upgrade-steps"]) == 0
    admin_head = current_rev("admin")
    registry_head = current_rev("registry")
    capsys.readouterr()

    code = run_mod.main(
        ["--db", "admin", "--db", "registry", "--direction", "down", "--target", "base"]
    )
    combined = "".join(capsys.readouterr())

    assert code == run_mod.EXIT_PARTIAL_DOWNGRADE
    assert current_rev("registry") is None
    assert current_rev("admin") == run_mod.SA_DROP_REVISION
    assert f"DOWNGRADE_STATUS registry rolled_back before={registry_head} after=-" in combined
    assert (
        f"DOWNGRADE_STATUS admin partial before={admin_head} after={run_mod.SA_DROP_REVISION}"
        in combined
    )


def test_disposable_stack_explicit_single_db_multi_revision_downgrade_and_reupgrade(
    disposable_migration_stack: Any,
) -> None:
    """Explicit multi-revision single-DB downgrade lands on target and re-upgrades cleanly."""
    current_rev = disposable_migration_stack
    assert run_mod.main(["--db", "control", "--skip-upgrade-steps"]) == 0
    head, _parent, grandparent = _head_and_ancestors("control", depth=3)
    assert current_rev("control") == head

    assert run_mod.main(["--db", "control", "--direction", "down", "--target", grandparent]) == 0
    assert current_rev("control") == grandparent

    assert run_mod.main(["--db", "control", "--skip-upgrade-steps"]) == 0
    assert current_rev("control") == head
