"""Every bulk or raw-SQL writer of ``credentials`` must invalidate the credential cache.

The broker's per-process credential-header cache
(``shared/credential_selection_cache.py``) is cleared when a transaction that
wrote a ``credentials`` row ends. Unit-of-work writes are seen by the ORM
mapper events; ORM bulk statements (``update(Credential)`` and friends) and raw
SQL (``text("UPDATE credentials …")``) are not. A function that issues one must
call ``mark_credentials_changed(session)`` itself, or a write served by this
process stays invisible to the cache for up to its TTL.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from .conftest import SRC_ROOT, python_files_in

MIGRATIONS_DIR = SRC_ROOT / "migrations"
_MARK = "mark_credentials_changed"
_DML_FUNCS = frozenset({"update", "delete", "insert"})
_RAW_DML = re.compile(
    r'\b(?:UPDATE|DELETE\s+FROM|INSERT\s+INTO)\s+(?:"?\w+"?\.)?"?credentials"?(?:\s|$|\()',
    re.IGNORECASE,
)


def _strings(node: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            found.append((n.lineno, n.value))
    return found


def _is_orm_dml(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call) or not node.args:
        return False
    func = node.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
    target = node.args[0]
    return name in _DML_FUNCS and isinstance(target, ast.Name) and target.id == "Credential"


def _calls_mark(node: ast.AST) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            func = n.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name == _MARK:
                return True
    return False


def _writes(node: ast.AST) -> list[int]:
    lines = [line for line, value in _strings(node) if _RAW_DML.search(value)]
    lines += [n.lineno for n in ast.walk(node) if isinstance(n, ast.Call) and _is_orm_dml(n)]
    return lines


def _check_file(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
    in_function: set[int] = set()
    for fn in functions:
        lines = _writes(fn)
        in_function.update(lines)
        if lines and not _calls_mark(fn):
            violations.extend(
                f"{path}:{line} — writes credentials without {_MARK}() in {fn.name}()"
                for line in lines
            )
    # A statement built at module level is checked against the whole file.
    module_level = [line for line in _writes(tree) if line not in in_function]
    if module_level and not _calls_mark(tree):
        violations.extend(
            f"{path}:{line} — module-level credentials write; no {_MARK}() in this file"
            for line in module_level
        )
    return violations


@pytest.mark.arch
def test_credential_writers_invalidate_the_selection_cache() -> None:
    violations: list[str] = []
    for path in python_files_in(SRC_ROOT):
        if MIGRATIONS_DIR in path.parents:
            continue
        violations.extend(_check_file(path))
    assert not violations, (
        "Bulk/raw-SQL writes to `credentials` must call mark_credentials_changed(session) "
        "(shared/credential_selection_cache.py) so the broker's credential cache is "
        "cleared:\n" + "\n".join(sorted(set(violations)))
    )


@pytest.mark.arch
def test_the_check_flags_unmarked_writers(tmp_path: Path) -> None:
    """The checker itself catches each writer shape and accepts a marked one."""
    source = tmp_path / "writer.py"
    source.write_text(
        "from sqlalchemy import text, update\n"
        "_STMT = text('DELETE FROM credentials WHERE id = :id')\n"
        "async def raw(session):\n"
        "    await session.execute(text('UPDATE control.credentials SET active = false'))\n"
        "async def bulk(session):\n"
        "    await session.execute(update(Credential).values(name='x'))\n"
        "async def marked(session):\n"
        "    mark_credentials_changed(session)\n"
        "    await session.execute(text('INSERT INTO credentials (id) VALUES (1)'))\n"
        "async def other(session):\n"
        "    await session.execute(text('UPDATE credential_refs SET x = 1'))\n"
    )
    violations = _check_file(source)
    assert any("raw()" in v for v in violations)
    assert any("bulk()" in v for v in violations)
    assert not any("marked()" in v or "other()" in v for v in violations)
    # The file calls the mark somewhere, so the module-level statement passes.
    assert not any("module-level" in v for v in violations)
