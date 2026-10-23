"""Keyset pagination predicates come from ``shared/db/keyset.py``, never hand-written.

A listing ordered by ``(sort key, id)`` resumes after the cursor row with
``sort < :s OR (sort = :s AND id < :id)``. Hand-writing that predicate invites
the drift behind #1956: a repo that compares the sort key alone skips every
row sharing the boundary timestamp. Repositories call
:func:`jentic_one.shared.db.keyset.apply_desc_keyset` (or one of the predicate
builders) instead.

The check flags, in any SQLAlchemy expression under ``src/jentic_one``:

- ``or_(a < x, and_(...))`` / ``or_(a > x, and_(...))``;
- ``(a < x) | ((...) & (...))`` (and the ``>`` form);
- a ``tuple_(...) < tuple_(...)`` row-value comparison (or ``>``, ``<=``, ``>=``).

Raw ``text()`` SQL is out of scope: the cross-database ``text()`` seams
(``control/repos/prerequisite_repo.py``, ``registry/repos/search/sqlite_lexical.py``)
cannot take a SQLAlchemy expression and spell the same predicate in SQL.
"""

from __future__ import annotations

import ast

import pytest

from .conftest import SRC_ROOT, python_files_in

_HELPER = SRC_ROOT / "shared" / "db" / "keyset.py"
_ORDERING = (ast.Lt, ast.Gt, ast.LtE, ast.GtE)


def _callee(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_ordering_compare(node: ast.expr) -> bool:
    return isinstance(node, ast.Compare) and isinstance(node.ops[0], _ORDERING)


def _is_and(node: ast.expr) -> bool:
    if isinstance(node, ast.Call) and _callee(node.func) == "and_":
        return True
    return isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitAnd)


def _violation(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call) and _callee(node.func) == "or_" and len(node.args) == 2:
        first, second = node.args
        if _is_ordering_compare(first) and _is_and(second):
            return "hand-written or_(sort < x, and_(sort == x, id < y))"
    if (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.BitOr)
        and _is_ordering_compare(node.left)
        and _is_and(node.right)
    ):
        return "hand-written (sort < x) | ((sort == x) & (id < y))"
    if (
        isinstance(node, ast.Compare)
        and isinstance(node.ops[0], _ORDERING)
        and isinstance(node.left, ast.Call)
        and _callee(node.left.func) == "tuple_"
    ):
        return "row-value tuple_(...) comparison"
    return None


def _check_source(source: str, filename: str) -> list[str]:
    tree = ast.parse(source, filename=filename)
    return [
        f"{filename}:{node.lineno} — {message}"  # type: ignore[attr-defined]
        for node in ast.walk(tree)
        if (message := _violation(node)) is not None
    ]


@pytest.mark.arch
def test_repos_use_shared_keyset_helper() -> None:
    """No module outside ``shared/db/keyset.py`` hand-writes a keyset predicate."""
    violations: list[str] = []
    for py_file in python_files_in(SRC_ROOT):
        if py_file == _HELPER:
            continue
        violations.extend(_check_source(py_file.read_text(encoding="utf-8"), str(py_file)))
    assert not violations, (
        "Keyset pagination predicate written by hand (use apply_desc_keyset / "
        "desc_keyset_predicate / asc_keyset_predicate from jentic_one.shared.db.keyset, "
        "which compare (sort key, id) so ties at a page boundary are not skipped):\n"
        + "\n".join(violations)
    )


@pytest.mark.arch
@pytest.mark.parametrize(
    "source",
    [
        pytest.param("or_(T.ts < c, and_(T.ts == c, T.id < i))\n", id="or-and-desc"),
        pytest.param("sa.or_(T.ts > c, sa.and_(T.ts == c, T.id > i))\n", id="or-and-asc"),
        pytest.param("(T.ts < c) | ((T.ts == c) & (T.id < i))\n", id="bitwise"),
        pytest.param("tuple_(T.ts, T.id) < tuple_(literal(c), literal(i))\n", id="row-value"),
    ],
)
def test_check_source_flags_hand_written_keyset(source: str) -> None:
    assert len(_check_source(source, "mod.py")) == 1


@pytest.mark.arch
@pytest.mark.parametrize(
    "source",
    [
        pytest.param("or_(T.a.is_(None), T.a <= now)\n", id="or-without-and"),
        pytest.param("or_(T.a == x, T.b.in_(ys))\n", id="or-equality"),
        pytest.param("stmt.where(apply_desc_keyset(stmt, T.ts, T.id, cursor))\n", id="helper"),
        pytest.param("(T.status == s) & (T.visible_at < now)\n", id="and-only"),
    ],
)
def test_check_source_ignores_other_predicates(source: str) -> None:
    assert _check_source(source, "mod.py") == []
