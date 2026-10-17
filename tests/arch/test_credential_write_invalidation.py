"""Every bulk or raw-SQL writer of ``credentials`` must invalidate the credential cache.

The broker's per-process credential-header cache
(``shared/credential_selection_cache.py``) is cleared when a transaction that
wrote a ``credentials`` row ends. Unit-of-work writes are seen by the ORM
mapper events; ORM bulk statements (``update(Credential)`` and friends) and raw
SQL (``text("UPDATE credentials …")``) are not. A function that issues one must
call ``mark_credentials_changed(session)`` itself, or a write served by this
process stays invisible to the cache for up to its TTL.

The check is static and covers these writer shapes:

- ``insert``/``update``/``delete`` and any ``*insert`` (``pg_insert``,
  ``sqlite_insert``) called on ``Credential`` under any import alias or dotted
  path (``schema.Credential``), on ``Credential.__table__`` or on
  ``table("credentials")``;
- ``.insert()``/``.update()``/``.delete()`` called on ``Credential.__table__``
  or ``table("credentials")``;
- ``bulk_insert_mappings``/``bulk_update_mappings`` with ``Credential``;
- SQL text naming the table in a string literal, or in an f-string whose
  placeholders are module-level string constants. A string that is a whole
  statement (a docstring) is not executed and is skipped.

A statement built at module or class level is checked in every function that
uses it.
Shapes it cannot see (``bulk_save_objects`` of ``Credential`` instances, a
table name assembled at run time) still need the mark.

Only this repo's ``src/`` is scanned: the jentic-one-enterprise repo is not, so
an enterprise writer of ``credentials`` must call ``mark_credentials_changed``
itself.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path

import pytest

from .conftest import SRC_ROOT, python_files_in

MIGRATIONS_DIR = SRC_ROOT / "migrations"
_MARK = "mark_credentials_changed"
_MODEL = "Credential"
_TABLE = "credentials"
_DML_FUNCS = frozenset({"update", "delete", "insert"})
_DML_METHODS = frozenset({"update", "delete", "insert"})
_BULK_FUNCS = frozenset({"bulk_insert_mappings", "bulk_update_mappings"})
_TABLE_FUNCS = frozenset({"table", "Table"})
_RAW_DML = re.compile(
    rf'\b(?:UPDATE|DELETE\s+FROM|INSERT\s+INTO)\s+(?:"?\w+"?\.)?"?{_TABLE}"?(?:\s|$|\()',
    re.IGNORECASE,
)


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


class _FileChecker:
    """Finds unmarked ``credentials`` writers in one parsed module."""

    def __init__(self, tree: ast.Module) -> None:
        self._tree = tree
        # ``from … import Credential as Cred`` makes ``Cred`` a model name too.
        self._models = {_MODEL}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == _MODEL and alias.asname:
                        self._models.add(alias.asname)
        # Module-level string constants, for f-string SQL such as f"UPDATE {_T} …".
        self._constants: dict[str, str] = {}
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self._constants[target.id] = node.value.value
        # A string that is a whole statement (a docstring, or a bare string used as
        # a comment) is never executed, so SQL-looking text in it is not a write.
        self._statement_strings = {
            id(node.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        }

    def _is_model(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in self._models
        # A dotted path ending in the model: ``schema.Credential``.
        return isinstance(node, ast.Attribute) and node.attr in self._models

    def _is_table(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Attribute) and node.attr == "__table__":
            return self._is_model(node.value)
        if isinstance(node, ast.Call) and _call_name(node) in _TABLE_FUNCS and node.args:
            first = node.args[0]
            return isinstance(first, ast.Constant) and first.value == _TABLE
        return False

    def _is_target(self, node: ast.AST) -> bool:
        return self._is_model(node) or self._is_table(node)

    def _is_orm_dml(self, node: ast.Call) -> bool:
        name = _call_name(node)
        if name is None:
            return False
        func = node.func
        # ``Credential.__table__.delete()`` / ``table("credentials").update()``.
        if name in _DML_METHODS and isinstance(func, ast.Attribute) and self._is_table(func.value):
            return True
        if not node.args:
            return False
        first = node.args[0]
        if name in _DML_FUNCS or name.endswith("insert"):
            return self._is_target(first)
        if name in _BULK_FUNCS:
            return self._is_model(first)
        return False

    def _sql_strings(self, node: ast.AST) -> Iterable[tuple[int, str]]:
        for n in ast.walk(node):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                if id(n) not in self._statement_strings:
                    yield n.lineno, n.value
            elif isinstance(n, ast.JoinedStr):
                parts: list[str] = []
                for value in n.values:
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        parts.append(value.value)
                    elif (
                        isinstance(value, ast.FormattedValue)
                        and isinstance(value.value, ast.Name)
                        and value.value.id in self._constants
                    ):
                        parts.append(self._constants[value.value.id])
                    else:
                        parts.append("?")
                yield n.lineno, "".join(parts)

    def writes(self, node: ast.AST) -> list[int]:
        lines = [line for line, value in self._sql_strings(node) if _RAW_DML.search(value)]
        lines += [
            n.lineno for n in ast.walk(node) if isinstance(n, ast.Call) and self._is_orm_dml(n)
        ]
        return lines

    @staticmethod
    def calls_mark(node: ast.AST) -> bool:
        return any(isinstance(n, ast.Call) and _call_name(n) == _MARK for n in ast.walk(node))

    def violations(self, path: Path) -> list[str]:
        found: list[str] = []
        functions = [
            n for n in ast.walk(self._tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        for fn in functions:
            lines = self.writes(fn)
            if lines and not self.calls_mark(fn):
                found.extend(
                    f"{path}:{line} — writes credentials without {_MARK}() in {fn.name}()"
                    for line in lines
                )
        # A statement built at module or class level is checked in each function using it.
        scopes: list[ast.Module | ast.ClassDef] = [self._tree]
        scopes += [n for n in ast.walk(self._tree) if isinstance(n, ast.ClassDef)]
        top_level = [stmt for scope in scopes for stmt in scope.body]
        for stmt in top_level:
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                continue
            lines = self.writes(stmt)
            if not lines:
                continue
            names = {
                t.id
                for n in ast.walk(stmt)
                if isinstance(n, ast.Assign | ast.AnnAssign)
                for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
                if isinstance(t, ast.Name)
            }
            users = [
                fn
                for fn in functions
                if any(
                    (isinstance(n, ast.Name) and n.id in names)
                    or (isinstance(n, ast.Attribute) and n.attr in names)
                    for n in ast.walk(fn)
                )
            ]
            unmarked = [fn.name for fn in users if not self.calls_mark(fn)]
            if not users:
                found.extend(
                    f"{path}:{line} — module-level credentials write used by no function "
                    f"that calls {_MARK}()"
                    for line in lines
                )
            for name in unmarked:
                found.extend(
                    f"{path}:{line} — module-level credentials write used in {name}() "
                    f"without {_MARK}()"
                    for line in lines
                )
        return found


def _check_file(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return _FileChecker(tree).violations(path)


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


_UNMARKED_WRITERS = {
    "raw_text": "await session.execute(text('UPDATE control.credentials SET active = false'))",
    "fstring": "await session.execute(text(f'DELETE FROM {_T} WHERE id = :id'))",
    "orm_update": "await session.execute(update(Credential).values(name='x'))",
    "dotted_model": "await session.execute(update(schema.Credential).values(name='x'))",
    "aliased_import": "await session.execute(delete(Cred).where(Cred.id == 'x'))",
    "table_delete": "await session.execute(Credential.__table__.delete())",
    "table_update": "await session.execute(Credential.__table__.update().values(name='x'))",
    "table_insert": "await session.execute(Credential.__table__.insert().values(id='x'))",
    "table_func": "await session.execute(table('credentials').delete())",
    "pg_insert": "await session.execute(pg_insert(Credential).on_conflict_do_nothing())",
    "sqlite_insert": "await session.execute(sqlite_insert(Credential).values(id='x'))",
    "bulk_mappings": "session.bulk_update_mappings(Credential, [{'id': 'x'}])",
}

_ALLOWED_SHAPES = {
    # Marked in the same function.
    "marked": "mark_credentials_changed(session)\n"
    "    await session.execute(update(Credential).values(name='x'))",
    # Another table, under every shape the checker inspects.
    "other_raw": "await session.execute(text('UPDATE credential_refs SET x = 1'))",
    "other_model": "await session.execute(update(CredentialRef).values(x=1))",
    "other_table": "await session.execute(table('credential_user_grants').delete())",
    "other_fstring": "await session.execute(text(f'DELETE FROM {_OTHER} WHERE id = 1'))",
    # Reads are not writes.
    "read": "await session.execute(select(Credential))",
}

_PRELUDE = (
    "from sqlalchemy import delete, select, table, text, update\n"
    "from sqlalchemy.dialects.postgresql import insert as pg_insert\n"
    "from sqlalchemy.dialects.sqlite import insert as sqlite_insert\n"
    "from jentic_one.control.core import schema\n"
    "from jentic_one.control.core.schema.credentials import Credential\n"
    "from jentic_one.control.core.schema.credentials import Credential as Cred\n"
    "_T = 'credentials'\n"
    "_OTHER = 'credential_refs'\n"
)


def _write_module(tmp_path: Path, bodies: dict[str, str], extra: str = "") -> Path:
    source = tmp_path / "writer.py"
    functions = "".join(
        f"async def {name}(session):\n    {body}\n" for name, body in bodies.items()
    )
    source.write_text(_PRELUDE + extra + functions)
    return source


@pytest.mark.arch
@pytest.mark.parametrize("shape", sorted(_UNMARKED_WRITERS))
def test_the_check_flags_an_unmarked_writer(tmp_path: Path, shape: str) -> None:
    violations = _check_file(_write_module(tmp_path, {shape: _UNMARKED_WRITERS[shape]}))
    assert any(f"{shape}()" in v for v in violations), violations


@pytest.mark.arch
def test_the_check_accepts_marked_writers_and_other_tables(tmp_path: Path) -> None:
    assert _check_file(_write_module(tmp_path, _ALLOWED_SHAPES)) == []


@pytest.mark.arch
def test_a_module_level_statement_is_checked_where_it_is_used(tmp_path: Path) -> None:
    """Each function using a module-level write statement must mark, not just one in the file."""
    extra = "_STMT = text('DELETE FROM credentials WHERE id = :id')\n"
    bodies = {
        "marked": "mark_credentials_changed(session)\n    await session.execute(_STMT)",
        "unmarked": "await session.execute(_STMT)",
        "unrelated": "mark_credentials_changed(session)",
    }
    violations = _check_file(_write_module(tmp_path, bodies, extra))
    assert any("used in unmarked()" in v for v in violations), violations
    assert not any("marked()" in v and "unmarked()" not in v for v in violations), violations

    orphan = _check_file(_write_module(tmp_path, {}, extra))
    assert any("used by no function" in v for v in orphan), orphan


@pytest.mark.arch
def test_sql_text_in_docstrings_and_bare_strings_is_not_a_write(tmp_path: Path) -> None:
    sql = "UPDATE credentials SET active = false"
    source = tmp_path / "docs_only.py"
    source.write_text(
        f'"""Module docstring: {sql}."""\n'
        f'class Repo:\n    """Class docstring: {sql}."""\n'
        f'    async def f(self, session):\n        """Runs {sql}."""\n'
        f'        "Bare string statement: DELETE FROM credentials."\n'
        "        return None\n"
    )
    assert _check_file(source) == []

    # A docstring does not hide a real write in the same function.
    bodies = {"documented": f'"""Runs {sql}."""\n    await session.execute(text("{sql}"))'}
    violations = _check_file(_write_module(tmp_path, bodies))
    assert any("documented()" in v for v in violations), violations
