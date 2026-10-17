"""Architecture enforcement: scoping boundary rules.

1. No repository may import Identity (repos are auth-agnostic).
2. No shared module may import ORM models from surface schemas.
3. Each surface scoping module only imports models from its own surface.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from .conftest import SRC_ROOT, python_files_in


def _collect_import_modules(tree: ast.AST) -> list[tuple[str, int]]:
    results: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                results.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.module:
            results.append((node.module, node.lineno))
    return results


def _check_no_import(source_dir: Path, forbidden_prefix: str) -> list[str]:
    violations: list[str] = []
    for py_file in python_files_in(source_dir):
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))
        for module, lineno in _collect_import_modules(tree):
            if module == forbidden_prefix or module.startswith(f"{forbidden_prefix}."):
                violations.append(f"{py_file}:{lineno} — imports '{module}'")
    return violations


@pytest.mark.arch
def test_repos_do_not_import_identity() -> None:
    """Repositories must never know about Identity."""
    identity_module = "jentic_one.shared.auth.identity"
    repo_dirs = [
        SRC_ROOT / "admin" / "repos",
        SRC_ROOT / "control" / "repos",
        SRC_ROOT / "registry" / "repos",
    ]
    violations: list[str] = []
    for repo_dir in repo_dirs:
        if repo_dir.exists():
            violations.extend(_check_no_import(repo_dir, identity_module))
    assert not violations, "Repositories import Identity:\n" + "\n".join(violations)


@pytest.mark.arch
def test_shared_web_identity_does_not_import_surface_orm() -> None:
    """The shared identity resolution module must not import surface ORM models."""
    identity_file = SRC_ROOT / "shared" / "web" / "identity.py"
    surface_schema_prefixes = [
        "jentic_one.admin.core.schema",
        "jentic_one.control.core.schema",
        "jentic_one.registry.core.schema",
    ]
    violations: list[str] = []
    if identity_file.exists():
        source = identity_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(identity_file))
        for module, lineno in _collect_import_modules(tree):
            for prefix in surface_schema_prefixes:
                if module == prefix or module.startswith(f"{prefix}."):
                    violations.append(
                        f"{identity_file}:{lineno} — imports '{module}' (shared must not "
                        f"couple to surface ORM models)"
                    )
    assert not violations, "Shared web/identity imports surface ORM models:\n" + "\n".join(
        violations
    )


@pytest.mark.arch
def test_scoping_modules_only_import_own_surface_models() -> None:
    """Each surface's scoping module must only reference its own surface's ORM models."""
    scoping_files = [
        ("admin", SRC_ROOT / "admin" / "scoping" / "filters.py"),
        ("admin", SRC_ROOT / "admin" / "scoping" / "actor_lookup.py"),
        ("control", SRC_ROOT / "control" / "scoping" / "filters.py"),
        ("registry", SRC_ROOT / "registry" / "scoping" / "filters.py"),
    ]
    all_surface_schema_prefixes = {
        "admin": "jentic_one.admin.core.schema",
        "control": "jentic_one.control.core.schema",
        "registry": "jentic_one.registry.core.schema",
    }

    violations: list[str] = []
    for surface, scoping_file in scoping_files:
        if not scoping_file.exists():
            continue
        source = scoping_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(scoping_file))
        for module, lineno in _collect_import_modules(tree):
            for other_surface, prefix in all_surface_schema_prefixes.items():
                if other_surface == surface:
                    continue
                if module == prefix or module.startswith(f"{prefix}."):
                    violations.append(
                        f"{scoping_file}:{lineno} — {surface} scoping imports "
                        f"'{module}' from {other_surface} surface"
                    )
    assert not violations, "Cross-surface model imports in scoping:\n" + "\n".join(violations)
