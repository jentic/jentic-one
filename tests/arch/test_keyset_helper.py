"""Keyset pagination predicates come from ``shared/db/keyset.py``, never hand-written.

A listing ordered by ``(sort key, id)`` resumes after the cursor row by
comparing both columns. A repo that compares the sort key alone
(``T.created_at < cursor``) skips every row sharing the boundary timestamp, and
a hand-written copy of the full comparison drifts from the helper. Repositories
call :func:`jentic_one.shared.db.keyset.apply_desc_keyset` /
:func:`~jentic_one.shared.db.keyset.apply_asc_keyset` (or one of the predicate
builders) instead.

The check flags, in any Python expression under ``src/jentic_one``:

- an ordering comparison (``<``, ``>``, ``<=``, ``>=``) between a column-like
  attribute (``T.ts``, ``subq.c.ts``) and a name starting with ``cursor``
  (``cursor``, ``cursor_ts``, ``cursor.distance``), on either side: the
  sort-key-only filter;
- ``or_(...)`` / ``a | b`` combining an ordering comparison with an
  ``and_(...)`` / ``a & b``, in either argument order: the hand-written
  expansion;
- a row-value ``tuple_(...)`` on either side of an ordering comparison.

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


def _root_name(node: ast.expr) -> str | None:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _is_cursor_value(node: ast.expr) -> bool:
    return (_root_name(node) or "").startswith("cursor")


def _is_column_like(node: ast.expr) -> bool:
    return isinstance(node, ast.Attribute) and not _is_cursor_value(node)


def _is_ordering_compare(node: ast.expr) -> bool:
    return isinstance(node, ast.Compare) and isinstance(node.ops[0], _ORDERING)


def _is_and(node: ast.expr) -> bool:
    if isinstance(node, ast.Call) and _callee(node.func) == "and_":
        return True
    return isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitAnd)


def _is_tuple_call(node: ast.expr) -> bool:
    return isinstance(node, ast.Call) and _callee(node.func) == "tuple_"


def _is_expansion(left: ast.expr, right: ast.expr) -> bool:
    return (_is_ordering_compare(left) and _is_and(right)) or (
        _is_and(left) and _is_ordering_compare(right)
    )


def _violation(node: ast.AST) -> str | None:
    if (
        isinstance(node, ast.Call)
        and _callee(node.func) == "or_"
        and len(node.args) == 2
        and _is_expansion(*node.args)
    ):
        return "hand-written or_(sort < x, and_(sort == x, id < y))"
    if (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.BitOr)
        and _is_expansion(node.left, node.right)
    ):
        return "hand-written (sort < x) | ((sort == x) & (id < y))"
    if isinstance(node, ast.Compare) and isinstance(node.ops[0], _ORDERING):
        sides = [node.left, *node.comparators]
        if any(_is_tuple_call(side) for side in sides):
            return "row-value tuple_(...) comparison"
        if any(_is_cursor_value(side) for side in sides) and any(
            _is_column_like(side) for side in sides
        ):
            return "sort-key comparison against a cursor value (drops the id tiebreak)"
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
        pytest.param("(c, i) > tuple_(T.ts, T.id)\n", id="row-value-rhs"),
        pytest.param("or_(and_(T.ts == c, T.id < i), T.ts < c)\n", id="or-and-reordered"),
        pytest.param("((T.ts == c) & (T.id < i)) | (T.ts < c)\n", id="bitwise-reordered"),
        pytest.param("stmt.where(T.created_at < cursor)\n", id="ts-only-cursor"),
        pytest.param("stmt.where(cursor_ts > subq.c.created_at)\n", id="ts-only-cursor-lhs"),
        pytest.param("stmt.where(T.d >= cursor.distance)\n", id="ts-only-cursor-attr"),
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
        pytest.param("T.created_at < cutoff\n", id="non-cursor-bound"),
        pytest.param("api_id > cursor_api_id\n", id="in-memory-cursor"),
        pytest.param("len(cursor) > limit\n", id="cursor-length"),
    ],
)
def test_check_source_ignores_other_predicates(source: str) -> None:
    assert _check_source(source, "mod.py") == []
