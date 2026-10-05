"""The theme-8 Phase-4 drop migration carries verbatim copies of job SQL.

Migrations must not import application code, so the drop gate
(``e2f3a4b5c6d7``) re-runs a copy of the retirement's "still needs a sweep"
query. If the copy drifted from the job's, the gate would refuse (or pass) rows
the pre-drop retirement had judged the other way. Pin them equal.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from jentic_one.control.repos.service_account_migration_repo import (
    SWEEPABLE_SQL,
    THEME8_RETIRED_SCOPES,
)

_MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "jentic_one"
    / "migrations"
    / "admin"
    / "versions"
    / "e2f3a4b5c6d7_drop_service_account_tables.py"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(_MIGRATION.stem, _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sweepable_sql_matches_the_job() -> None:
    assert _load().SWEEPABLE_SQL == SWEEPABLE_SQL


def test_retired_scopes_match_the_job() -> None:
    assert _load()._RETIRED_SCOPES == THEME8_RETIRED_SCOPES


def test_retired_scope_probe_covers_every_retired_scope() -> None:
    migration = _load()
    needle = migration._RETIRED_SCOPE_PROBE.strip("%")
    assert all(needle in scope for scope in migration._RETIRED_SCOPES)
