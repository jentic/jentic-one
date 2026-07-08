"""Enforce ORM model conventions across the codebase.

All models inheriting from RegistryBase, ControlBase, or AdminBase must follow
project naming and structural conventions.

Enforced facts are sourced from the rules repo's ``orm.facts.yaml`` (see
``_rules_facts.py``); do not hard-code them here.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from ._rules_facts import orm_facts
from .conftest import SRC_ROOT, python_files_in

_FACTS = orm_facts()

VALID_BASES = frozenset(_FACTS["valid_bases"])

# Mixins that provide audit columns (created_at, updated_at, created_by) to any
# model that inherits them. A model gaining created_at via one of these mixins
# satisfies the created_at convention without declaring the column directly.
AUDIT_MIXINS = frozenset(_FACTS["audit_mixins"])

_TABLENAME_FACTS = _FACTS["tablename"]
_SNAKE_CASE_REQUIRED = bool(_TABLENAME_FACTS["snake_case"])
_PLURAL_SUFFIXES = tuple(_TABLENAME_FACTS["plural_suffixes"])
_REQUIRED_COLUMNS = tuple(_FACTS["required_columns"])


def _get_base_name(node: ast.expr) -> str | None:
    """Extract the base class name from a class definition base node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _find_orm_classes(tree: ast.AST) -> list[ast.ClassDef]:
    """Find all class definitions inheriting from an ORM base."""
    results: list[ast.ClassDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            name = _get_base_name(base)
            if name in VALID_BASES:
                results.append(node)
                break
    return results


def _get_tablename(cls_node: ast.ClassDef) -> str | None:
    """Extract __tablename__ value from a class body."""
    for stmt in cls_node.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "__tablename__"
                    and isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)
                ):
                    return stmt.value.value
    return None


def _is_snake_case(name: str) -> bool:
    """Check if a string is snake_case (lowercase, underscores, no leading/trailing _)."""
    return bool(re.match(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$", name))


def _get_column_names(cls_node: ast.ClassDef) -> list[str]:
    """Extract annotated assignment names (Mapped columns) from a class."""
    names: list[str] = []
    for stmt in cls_node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            names.append(stmt.target.id)
    return names


def _has_base(cls_node: ast.ClassDef, names: frozenset[str]) -> bool:
    """Return True if the class lists any of the given base class names."""
    return any(_get_base_name(base) in names for base in cls_node.bases)


def _check_model(filepath: Path, cls_node: ast.ClassDef) -> list[str]:
    """Check a single ORM model class for convention violations."""
    violations: list[str] = []
    class_name = cls_node.name
    loc = f"{filepath}:{cls_node.lineno}"

    tablename = _get_tablename(cls_node)
    if tablename is None:
        violations.append(
            f"{loc} — class {class_name} missing __tablename__ "
            f"(all ORM models must set __tablename__ explicitly)"
        )
    else:
        if _SNAKE_CASE_REQUIRED and not _is_snake_case(tablename):
            violations.append(
                f"{loc} — class {class_name} has __tablename__ = '{tablename}' "
                f"(table names must be snake_case, e.g. 'api_specs')"
            )

        if not tablename.endswith(_PLURAL_SUFFIXES):
            violations.append(
                f"{loc} — class {class_name} has __tablename__ = '{tablename}' "
                f"(table names should be plural, e.g. 'jobs' not 'job')"
            )

    columns = _get_column_names(cls_node)

    if "id" in _REQUIRED_COLUMNS and "id" not in columns:
        violations.append(
            f"{loc} — class {class_name} missing 'id' column "
            f"(all tables must have a primary key named 'id')"
        )

    # created_at may be declared directly or inherited from an audit mixin.
    if (
        "created_at" in _REQUIRED_COLUMNS
        and "created_at" not in columns
        and not _has_base(cls_node, AUDIT_MIXINS)
    ):
        violations.append(
            f"{loc} — class {class_name} missing 'created_at' column "
            f"(all tables must have a created_at timestamp)"
        )

    return violations


def _check_file(filepath: Path) -> list[str]:
    """Check all ORM models in a file."""
    source = filepath.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return []

    violations: list[str] = []
    for cls_node in _find_orm_classes(tree):
        violations.extend(_check_model(filepath, cls_node))
    return violations


@pytest.mark.arch
def test_orm_models_have_tablename() -> None:
    """All ORM models must set __tablename__ explicitly."""
    violations: list[str] = []
    for py_file in python_files_in(SRC_ROOT):
        if py_file.is_relative_to(SRC_ROOT / "migrations"):
            continue
        violations.extend(v for v in _check_file(py_file) if "__tablename__" in v)
    assert not violations, "ORM models missing __tablename__:\n" + "\n".join(violations)


@pytest.mark.arch
def test_orm_table_names_are_snake_case_plural() -> None:
    """Table names must be snake_case and plural."""
    violations: list[str] = []
    for py_file in python_files_in(SRC_ROOT):
        if py_file.is_relative_to(SRC_ROOT / "migrations"):
            continue
        violations.extend(v for v in _check_file(py_file) if "snake_case" in v or "plural" in v)
    assert not violations, "ORM table naming violations:\n" + "\n".join(violations)


@pytest.mark.arch
def test_orm_models_have_id_column() -> None:
    """All ORM models must have a UUID primary key named 'id'."""
    violations: list[str] = []
    for py_file in python_files_in(SRC_ROOT):
        if py_file.is_relative_to(SRC_ROOT / "migrations"):
            continue
        violations.extend(v for v in _check_file(py_file) if "missing 'id' column" in v)
    assert not violations, "ORM models missing 'id' primary key:\n" + "\n".join(violations)


@pytest.mark.arch
def test_orm_models_have_created_at() -> None:
    """All ORM models must have a created_at timestamp."""
    violations: list[str] = []
    for py_file in python_files_in(SRC_ROOT):
        if py_file.is_relative_to(SRC_ROOT / "migrations"):
            continue
        violations.extend(v for v in _check_file(py_file) if "missing 'created_at' column" in v)
    assert not violations, "ORM models missing 'created_at' timestamp:\n" + "\n".join(violations)


EXEMPT_FROM_KSUID = frozenset(_FACTS["ksuid_exempt_tables"])


def _check_ksuid_default(filepath: Path, cls_node: ast.ClassDef) -> list[str]:
    """Check that the id column uses generate_ksuid server_default."""
    violations: list[str] = []
    tablename = _get_tablename(cls_node)
    if tablename is None or tablename in EXEMPT_FROM_KSUID:
        return violations

    loc = f"{filepath}:{cls_node.lineno}"
    class_name = cls_node.name

    for stmt in cls_node.body:
        if not isinstance(stmt, ast.AnnAssign):
            continue
        if not isinstance(stmt.target, ast.Name) or stmt.target.id != "id":
            continue
        if stmt.value is None:
            violations.append(f"{loc} — class {class_name} 'id' column has no mapped_column call")
            break

        if not isinstance(stmt.value, ast.Call):
            break

        for kw in stmt.value.keywords:
            if kw.arg != "server_default":
                continue
            if _is_generate_ksuid_call(kw.value):
                return violations
            violations.append(
                f"{loc} — class {class_name} 'id' column must use "
                f"server_default=func.generate_ksuid(...), not another default"
            )
            return violations

        violations.append(
            f"{loc} — class {class_name} 'id' column missing "
            f"server_default=func.generate_ksuid(...)"
        )
        break

    return violations


def _is_generate_ksuid_call(node: ast.expr) -> bool:
    """Check if an AST node is a call to func.generate_ksuid(...)."""
    if not isinstance(node, ast.Call):
        return False
    func_node = node.func
    return isinstance(func_node, ast.Attribute) and func_node.attr == "generate_ksuid"


@pytest.mark.arch
def test_orm_id_uses_ksuid() -> None:
    """Non-exempt ORM models must use generate_ksuid for their id server_default."""
    if _FACTS["primary_key"] != "ksuid":
        pytest.skip(f"primary_key strategy is {_FACTS['primary_key']!r}, not 'ksuid'")
    violations: list[str] = []
    for py_file in python_files_in(SRC_ROOT):
        if py_file.is_relative_to(SRC_ROOT / "migrations"):
            continue
        source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue
        for cls_node in _find_orm_classes(tree):
            violations.extend(_check_ksuid_default(py_file, cls_node))
    assert not violations, "ORM models not using generate_ksuid for id:\n" + "\n".join(violations)
