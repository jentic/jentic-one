"""The grant-expiry upgrade scans execution history before it takes any DDL lock.

Adding a column or index locks its table until the migration commits, so the
last-use aggregate over ``execution_records`` must run first; behind the
``held_executions`` DDL it would stall approval reads and decisions for as long
as the scan takes.
"""

from __future__ import annotations

from pathlib import Path

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "src/jentic_one/migrations/admin/versions/a3f7c1e9d5b2_add_grant_expiry.py"
)
_SOURCE = _MIGRATION.read_text()
_UPGRADE = _SOURCE.split("def upgrade()", 1)[1].split("def _as_utc", 1)[0]


def test_the_last_use_aggregate_runs_before_the_held_execution_ddl() -> None:
    aggregate = _UPGRADE.index("sa.func.max(_RECORDS.c.started_at)")
    held_ddl = _UPGRADE.index('"held_executions"')
    assert aggregate < held_ddl


def test_the_last_use_aggregate_runs_before_any_binding_column_is_added() -> None:
    aggregate = _UPGRADE.index("sa.func.max(_RECORDS.c.started_at)")
    add_columns = _UPGRADE.index('op.add_column("agent_credential_bindings"')
    assert aggregate < add_columns
