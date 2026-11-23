"""Enforce that every record_audit / record_audit_best_effort call passes an origin kwarg.

This prevents regression where new audit call sites forget to propagate origin.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from .conftest import SRC_ROOT, python_files_in

_AUDIT_FUNCS = frozenset({"record_audit", "record_audit_best_effort"})

_EXCLUDED = frozenset(
    {
        SRC_ROOT / "shared" / "audit" / "__init__.py",
    }
)


pytestmark = pytest.mark.arch


def _find_missing_origin(filepath: Path) -> list[str]:
    """Return violation messages for audit calls missing the origin kwarg."""
    source = filepath.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return []

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        func_name: str | None = None
        if isinstance(func, ast.Name):
            func_name = func.id
        elif isinstance(func, ast.Attribute):
            func_name = func.attr

        if func_name not in _AUDIT_FUNCS:
            continue

        kwarg_names = {kw.arg for kw in node.keywords if kw.arg is not None}
        if "origin" not in kwarg_names:
            violations.append(
                f"{filepath.relative_to(SRC_ROOT)}:{node.lineno} "
                f"— {func_name}() missing origin= kwarg"
            )
    return violations


def _all_service_files() -> list[Path]:
    """Collect all Python files that might contain audit calls."""
    dirs = [
        SRC_ROOT / "auth" / "services",
        SRC_ROOT / "admin" / "services",
        SRC_ROOT / "control" / "services",
        SRC_ROOT / "registry" / "services",
        SRC_ROOT / "broker" / "services",
    ]
    files: list[Path] = []
    for d in dirs:
        if d.exists():
            files.extend(python_files_in(d))
    return [f for f in files if f not in _EXCLUDED]


@pytest.mark.parametrize(
    "filepath",
    _all_service_files(),
    ids=lambda p: str(p.relative_to(SRC_ROOT)),
)
def test_audit_calls_pass_origin(filepath: Path) -> None:
    """Every record_audit* call in service files must pass origin= explicitly."""
    violations = _find_missing_origin(filepath)
    assert not violations, "\n".join(violations)
