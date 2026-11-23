"""Transaction-scoped advisory locks must go through ``shared/db/locks.py``.

No module under ``src/jentic_one`` may embed a ``pg_advisory_xact_lock`` or
``pg_advisory_lock`` SQL literal directly, except:

- ``shared/db/locks.py`` — the transaction-scoped advisory-lock primitive;
- ``shared/db/session.py`` — the session-level run-lock context managers;
- ``migrations/`` — frozen historical migration copies.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.arch.conftest import SRC_ROOT, python_files_in

pytestmark = pytest.mark.arch

_EXEMPT_FILES = frozenset(
    {
        SRC_ROOT / "shared" / "db" / "locks.py",
        SRC_ROOT / "shared" / "db" / "session.py",
    }
)

_FORBIDDEN_LITERALS = ("pg_advisory_xact_lock", "pg_advisory_lock")


def _is_exempt(path: Path) -> bool:
    if path in _EXEMPT_FILES:
        return True
    return "migrations" in path.parts


def _check_source(source: str, filename: str) -> list[str]:
    tree = ast.parse(source, filename=filename)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            docstrings.add(id(node.body[0].value))

    violations: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            for literal in _FORBIDDEN_LITERALS:
                if literal in node.value:
                    violations.append(
                        f"{filename}:{node.lineno} — raw {literal!r} string literal "
                        "(use advisory_xact_lock from jentic_one.shared.db.locks)"
                    )
    return violations


def test_advisory_lock_sql_only_in_shared_db() -> None:
    """No non-exempt module under src/jentic_one embeds pg_advisory_xact_lock / pg_advisory_lock."""
    violations: list[str] = []
    for py_file in sorted(python_files_in(SRC_ROOT)):
        if _is_exempt(py_file):
            continue
        rel = str(py_file.relative_to(SRC_ROOT))
        violations.extend(_check_source(py_file.read_text(encoding="utf-8"), rel))
    assert not violations, (
        "Direct pg_advisory_xact_lock / pg_advisory_lock SQL found outside shared/db/locks.py:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_check_source_flags_advisory_lock_sql_literal() -> None:
    src = 'from sqlalchemy import text\nstmt = text("SELECT pg_advisory_xact_lock(hashtext(:k))")\n'
    assert len(_check_source(src, "repo.py")) == 1


def test_check_source_ignores_docstrings() -> None:
    src = 'def f() -> None:\n    """On Postgres uses pg_advisory_xact_lock."""\n    pass\n'
    assert _check_source(src, "repo.py") == []
