"""Arch test: every built-in search strategy uses the shared filter contract.

Composite strategies (e.g. a lexical + semantic fusion) are only correct if
every child applies the *same* pin/active-state/api-filter predicate. That
predicate has one source of truth — ``jentic_one.registry.repos.search.filters``
(``ACTIVE_REVISION_STATES`` / ``search_filter_clause``) — so this gate fails
when a registered strategy restates the revision states itself instead of
consuming the contract.

The behavioural half of the contract (identical hit sets for the same pins,
states and api filters) is pinned by
``tests/integration/registry/ingest/test_search_filter_parity.py``.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import jentic_one.registry.repos.search as search_pkg
from jentic_one.registry.repos.search.registry import _STRATEGIES
from tests.arch.conftest import SRC_ROOT

pytestmark = pytest.mark.arch

FILTERS_MODULE = "jentic_one.registry.repos.search.filters"
CONTRACT_NAMES = {"ACTIVE_REVISION_STATES", "search_filter_clause"}

#: The public composition + filter-contract surface composites depend on.
REQUIRED_EXPORTS = {
    "ACTIVE_REVISION_STATES",
    "resolve_strategy_by_name",
    "search_filter_clause",
}


def _builtin_strategy_files() -> list[Path]:
    """Source files of every registered strategy shipped in this package."""
    files: set[Path] = set()
    for cls in _STRATEGIES.values():
        source = inspect.getsourcefile(cls)
        if source is None:
            continue
        path = Path(source).resolve()
        if path.is_relative_to(SRC_ROOT):
            files.add(path)
    return sorted(files)


def _contract_imports(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {
            FILTERS_MODULE,
            search_pkg.__name__,
        }:
            names.update(alias.name for alias in node.names)
    return names & CONTRACT_NAMES


def _restated_states(tree: ast.Module) -> list[int]:
    """Line numbers referencing ``ApiRevisionState`` (a restated state set)."""
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Name | ast.Attribute)
        and (getattr(node, "id", None) or getattr(node, "attr", None)) == "ApiRevisionState"
    )


def test_builtin_strategies_are_discovered() -> None:
    files = _builtin_strategy_files()
    assert files, "no built-in registered search strategies found — has the registry moved?"


@pytest.mark.parametrize(
    "strategy_file", _builtin_strategy_files(), ids=lambda p: p.relative_to(SRC_ROOT).as_posix()
)
def test_strategy_uses_shared_filter_contract(strategy_file: Path) -> None:
    rel = strategy_file.relative_to(SRC_ROOT)
    tree = ast.parse(strategy_file.read_text(), filename=str(strategy_file))

    assert _contract_imports(tree), (
        f"{rel} — registered search strategy does not import ACTIVE_REVISION_STATES or "
        f"search_filter_clause from {FILTERS_MODULE} (every strategy must apply the shared "
        "pin/active-state/api-filter contract so composite strategies stay filter-identical)"
    )
    restated = _restated_states(tree)
    assert not restated, (
        f"{rel}:{restated[0]} — registered search strategy references ApiRevisionState "
        "directly (use ACTIVE_REVISION_STATES / search_filter_clause from "
        f"{FILTERS_MODULE} instead of restating the active revision states)"
    )


def test_search_package_exports_composition_seam() -> None:
    missing = sorted(REQUIRED_EXPORTS - set(search_pkg.__all__))
    assert not missing, (
        f"jentic_one.registry.repos.search.__all__ is missing {missing} — composite "
        "strategies in downstream packages depend on these public names"
    )
    unresolved = sorted(n for n in search_pkg.__all__ if not hasattr(search_pkg, n))
    assert not unresolved, f"jentic_one.registry.repos.search.__all__ has unresolved {unresolved}"
