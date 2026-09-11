"""Architecture enforcement: mandatory query scoping coverage.

1. Every model referenced in a surface's scoping/filters.py must appear in that
   surface's scoping dicts (_OWNER_MODELS / _DELEGATION_SCOPES).
2. No service in control/ or admin/ (the scoped surfaces) may declare
   `identity: Identity | None` — identity must be mandatory.
3. verify_only_presence must not appear anywhere in the codebase.
"""

from __future__ import annotations

import ast

import pytest

from jentic_one.admin.scoping.filters import _OWNER_MODELS as _ADMIN_OWNER_MODELS
from jentic_one.control.scoping.filters import _DELEGATION_SCOPES as _CONTROL_DELEGATION_SCOPES
from jentic_one.control.scoping.filters import _OWNER_MODELS as _CONTROL_OWNER_MODELS

from .conftest import SRC_ROOT, python_files_in


@pytest.mark.arch
def test_no_verify_only_presence() -> None:
    """The verify_only_presence sentinel must not exist anywhere."""
    violations: list[str] = []
    for py_file in python_files_in(SRC_ROOT):
        source = py_file.read_text(encoding="utf-8")
        if "verify_only_presence" in source:
            for i, line in enumerate(source.splitlines(), 1):
                if "verify_only_presence" in line:
                    violations.append(f"{py_file}:{i}")
    assert not violations, "verify_only_presence still referenced:\n" + "\n".join(violations)


@pytest.mark.arch
def test_no_optional_identity_in_scoped_services() -> None:
    """Service methods in control/ and admin/ must not have optional identity params."""
    scoped_service_dirs = [
        SRC_ROOT / "control" / "services",
        SRC_ROOT / "admin" / "services",
        SRC_ROOT / "auth" / "services",
    ]
    violations: list[str] = []
    for svc_dir in scoped_service_dirs:
        if not svc_dir.exists():
            continue
        for py_file in python_files_in(svc_dir):
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for arg in node.args.args + node.args.kwonlyargs:
                    if arg.arg != "identity":
                        continue
                    annotation = arg.annotation
                    if annotation is None:
                        continue
                    ann_src = ast.unparse(annotation)
                    if "None" in ann_src:
                        violations.append(
                            f"{py_file}:{node.lineno} — {node.name}() has optional identity"
                        )
    assert not violations, "Optional identity in scoped services:\n" + "\n".join(violations)


@pytest.mark.arch
def test_control_scoping_model_completeness() -> None:
    """All ORM model imports in control/scoping/filters.py must be in scoping dicts."""
    covered = set(_CONTROL_OWNER_MODELS) | set(_CONTROL_DELEGATION_SCOPES)

    filters_file = SRC_ROOT / "control" / "scoping" / "filters.py"
    source = filters_file.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(filters_file))

    imported_models: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "schema" in node.module:
            for alias in node.names:
                imported_models.append(alias.name)

    covered_names = {cls.__name__ for cls in covered}
    missing = [m for m in imported_models if m not in covered_names]
    assert not missing, f"Models imported but not in scoping dicts: {missing}"


@pytest.mark.arch
def test_admin_scoping_model_completeness() -> None:
    """All ORM model imports in admin/scoping/filters.py must be in _OWNER_MODELS."""
    filters_file = SRC_ROOT / "admin" / "scoping" / "filters.py"
    source = filters_file.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(filters_file))

    imported_models: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "schema" in node.module:
            for alias in node.names:
                imported_models.append(alias.name)

    covered_names = {cls.__name__ for cls in _ADMIN_OWNER_MODELS}
    missing = [m for m in imported_models if m not in covered_names]
    assert not missing, f"Models imported but not in scoping dicts: {missing}"
