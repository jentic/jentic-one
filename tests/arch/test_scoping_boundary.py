"""Architecture enforcement: scoping boundary rules.

1. No repository may import Identity (repos are auth-agnostic).
2. No shared module may import ORM models from surface schemas.
3. Each surface scoping module only imports models from its own surface.
4. A scoped load in a mutating service method uses a write filter.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from .conftest import SRC_ROOT, python_files_in

_Func = ast.FunctionDef | ast.AsyncFunctionDef
_WRITE_MUTATOR = re.compile(
    r"^(update|delete|set_|replace|revoke|grant|bind|decide|withdraw|mark_)"
)
_WRITE_SERVICE_DIRS = [
    SRC_ROOT / "admin" / "services",
    SRC_ROOT / "auth" / "services",
    SRC_ROOT / "control" / "services",
    SRC_ROOT / "registry" / "services",
]

#: ``Class.method`` -> why its read filter is sufficient for this mutation.
_WRITE_FILTER_ALLOWLIST: dict[str, str] = {
    "PermissionService.set_assigned": (
        "User filters have no read widenings; ensure_can_manage authorizes the mutation"
    ),
    "UserService.update": (
        "User filters have no read widenings; ensure_can_manage authorizes the mutation"
    ),
}


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


def _name_of(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _has_repository_mutator(method: _Func) -> bool:
    for node in ast.walk(method):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        receiver = _name_of(node.func.value)
        if (
            receiver is not None
            and receiver.endswith("Repository")
            and _WRITE_MUTATOR.match(node.func.attr)
        ):
            return True
    return False


def _uses_write_filter(call: ast.Call) -> bool:
    return any(
        kw.arg == "for_write" and isinstance(kw.value, ast.Constant) and kw.value.value is True
        for kw in call.keywords
    )


def _write_filter_findings(tree: ast.Module, py_file: Path) -> dict[str, tuple[Path, int, bool]]:
    """Return mutating methods with scoped loads and whether every load uses a write filter."""
    found: dict[str, tuple[Path, int, bool]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or not node.name.endswith("Service"):
            continue
        for method in node.body:
            if not isinstance(method, _Func) or not _has_repository_mutator(method):
                continue
            builds = [
                call
                for call in ast.walk(method)
                if isinstance(call, ast.Call) and _name_of(call.func) == "build_access_filters"
            ]
            if builds:
                found[f"{node.name}.{method.name}"] = (
                    py_file,
                    method.lineno,
                    all(_uses_write_filter(call) for call in builds),
                )
    return found


def _write_filter_findings_in_services() -> dict[str, tuple[Path, int, bool]]:
    found: dict[str, tuple[Path, int, bool]] = {}
    for services_dir in _WRITE_SERVICE_DIRS:
        for py_file in python_files_in(services_dir):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            found.update(_write_filter_findings(tree, py_file))
    return found


@pytest.mark.arch
def test_write_paths_use_for_write() -> None:
    """A scoped load in a method with a repository mutation opts out of read widenings."""
    violations = [
        f"{path}:{line} — {name} mutates a repository after build_access_filters; "
        "pass for_write=True or add _WRITE_FILTER_ALLOWLIST with a reason"
        for name, (path, line, scoped) in sorted(_write_filter_findings_in_services().items())
        if not scoped and name not in _WRITE_FILTER_ALLOWLIST
    ]
    assert not violations, "Read filters on write paths:\n" + "\n".join(violations)


@pytest.mark.arch
def test_write_filter_allowlist_is_not_stale() -> None:
    """Write-filter exemptions disappear when the method changes or opts into write scoping."""
    methods = _write_filter_findings_in_services()
    stale = [
        f"{name}: " + ("no matching mutating method" if name not in methods else "now scoped")
        for name in sorted(_WRITE_FILTER_ALLOWLIST)
        if name not in methods or methods[name][2]
    ]
    assert not stale, "Remove stale _WRITE_FILTER_ALLOWLIST entries:\n" + "\n".join(stale)


@pytest.mark.arch
def test_write_filter_check_rejects_read_filter_in_write_method() -> None:
    """Self-check: a repository mutator makes a read-filtered load a violation."""
    tree = ast.parse(
        "class ExampleService:\n"
        "    async def update(self, thing_id: str, *, identity: Identity):\n"
        "        filters = build_access_filters(identity, Thing)\n"
        "        await ThingRepository.update(thing_id, filters=filters)\n"
    )
    methods = _write_filter_findings(tree, Path("<self-check>"))
    assert methods["ExampleService.update"][2] is False


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
