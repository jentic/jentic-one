"""Shared fixtures for the shared-infrastructure unit tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import Table
from sqlalchemy.engine import Connection
from sqlalchemy.schema import CreateTable

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.held_executions import HeldExecution
from jentic_one.admin.core.schema.job_results import JobResult
from jentic_one.admin.core.schema.jobs import Job
from jentic_one.shared.config import DatabaseConfig
from jentic_one.shared.db.session import DatabaseSession

_JOB_TABLES = (
    cast(Table, Job.__table__),
    cast(Table, JobResult.__table__),
    cast(Table, HeldExecution.__table__),
    cast(Table, Event.__table__),
)


def _create_tables(sync_conn: Connection, *tables: Table) -> None:
    """Create ``tables`` on SQLite, stripping the Postgres-only server defaults.

    Callers supply every value those defaults would (e.g. ``attempts``,
    ``actor_type``); the ORM models' Python-side defaults cover the rest.
    """
    for table in tables:
        saved = {col: col.server_default for col in table.columns}
        for col in table.columns:
            col.server_default = None
        try:
            sync_conn.execute(CreateTable(table, if_not_exists=True))
        finally:
            for col, default in saved.items():
                col.server_default = default


@pytest.fixture()
async def jobs_db(tmp_path: Path) -> AsyncGenerator[DatabaseSession, None]:
    """A real file-backed SQLite admin DB holding the worker's job tables."""
    db = DatabaseSession(DatabaseConfig(backend="sqlite", path=str(tmp_path / "admin.db")))
    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(_create_tables, *_JOB_TABLES)
    yield db
    await db.close()
