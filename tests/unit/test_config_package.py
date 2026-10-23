"""The ``jentic_one.shared.config`` package re-exports its submodules' public API."""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path
from types import ModuleType

import pytest

from jentic_one.shared import config as config_package

_SUBMODULES: list[ModuleType] = [
    importlib.import_module(f"{config_package.__name__}.{info.name}")
    for info in pkgutil.iter_modules(config_package.__path__)
]


def _public_names(module: ModuleType) -> set[str]:
    """Public top-level names a submodule defines itself (not ones it imports)."""
    assert module.__file__ is not None
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return {name for name in names if not name.startswith("_")}


def test_submodules_are_discovered() -> None:
    assert len(_SUBMODULES) > 1


@pytest.mark.parametrize("module", _SUBMODULES, ids=lambda m: m.__name__.rsplit(".", 1)[-1])
def test_public_names_are_reexported(module: ModuleType) -> None:
    """Every public name a submodule defines is importable from the package."""
    missing = sorted(
        name
        for name in _public_names(module)
        if getattr(config_package, name, None) is not getattr(module, name)
        or name not in config_package.__all__
    )
    assert not missing, f"{module.__name__} names not re-exported: {missing}"


def test_all_entries_resolve() -> None:
    unresolved = [name for name in config_package.__all__ if not hasattr(config_package, name)]
    assert not unresolved
