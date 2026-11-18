"""Arch test: services and web layers must not import strategy modules directly.

They should use the strategy registry (resolve_strategy) instead of importing
concrete strategy implementations.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.arch.conftest import SRC_ROOT, python_files_in

FORBIDDEN_MODULES = {
    "jentic_one.registry.repos.search.postgres_lexical",
    "jentic_one.registry.repos.search.sqlite_lexical",
}

SCANNED_DIRS = [
    SRC_ROOT / "registry" / "services",
    SRC_ROOT / "registry" / "web",
]


pytestmark = pytest.mark.arch


def _imported_modules(path: Path) -> set[str]:
    """Return all imported module names from a Python file."""
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


@pytest.mark.parametrize("scan_dir", SCANNED_DIRS, ids=lambda p: p.name)
def test_no_direct_strategy_imports(scan_dir: Path) -> None:
    """No service or web module should import strategy implementations directly."""
    violations: list[str] = []
    for py_file in python_files_in(scan_dir):
        imports = _imported_modules(py_file)
        for forbidden in FORBIDDEN_MODULES:
            if forbidden in imports:
                rel = py_file.relative_to(SRC_ROOT)
                violations.append(f"{rel} imports {forbidden}")

    assert not violations, "Direct strategy imports found:\n" + "\n".join(violations)
