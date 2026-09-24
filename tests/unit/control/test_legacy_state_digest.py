"""The Phase-6b drop migrations carry verbatim copies of ``legacy_state_digest``.

Migrations must not import application code, so the digest the
``flatten-toolkits --verify --acknowledge`` writer records and the one each
drop gate recomputes are separate functions. If they ever diverged, every
acknowledgement would read as stale and the drops would refuse forever (or,
worse, a changed algorithm could collide). Pin them equal.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from jentic_one.control.repos.toolkit_flattening_repo import legacy_state_digest

_VERSIONS = Path(__file__).resolve().parents[3] / "src" / "jentic_one" / "migrations"
_MIGRATIONS = (
    _VERSIONS / "control" / "versions" / "v3d4e5f6a7b8_drop_toolkit_tables.py",
    _VERSIONS / "admin" / "versions" / "d1e2f3a4b5c6_drop_agent_toolkit_bindings_sweep_scopes.py",
)

_CASES: tuple[dict[str, list[str]], ...] = (
    {},
    {"toolkits": []},
    {"toolkits": ["tk_b", "tk_a"], "toolkit_credential_bindings": ["tcb_1"]},
    {"agent_toolkit_bindings": ["atb_2", "atb_1", "atb_3"]},
)


def _load(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("path", _MIGRATIONS, ids=lambda p: p.stem.split("_", 1)[0])
@pytest.mark.parametrize("ids_by_table", _CASES)
def test_migration_digest_matches_the_writer(
    path: Path, ids_by_table: dict[str, list[str]]
) -> None:
    migration = _load(path)
    assert migration._legacy_state_digest(ids_by_table) == legacy_state_digest(ids_by_table)


def test_digest_is_order_independent_and_table_qualified() -> None:
    assert legacy_state_digest({"toolkits": ["a", "b"]}) == legacy_state_digest(
        {"toolkits": ["b", "a"]}
    )
    assert legacy_state_digest({"toolkits": ["a"]}) != legacy_state_digest(
        {"toolkit_credential_bindings": ["a"]}
    )
    assert legacy_state_digest({"toolkits": ["a"]}) != legacy_state_digest({"toolkits": ["a", "b"]})
