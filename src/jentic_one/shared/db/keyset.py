"""Keyset (seek) pagination predicates over a ``(sort key, id)`` pair.

A listing ordered by ``(sort_col, id_col)`` resumes after the last row it
returned by comparing both columns: rows that share the sort key are ordered by
``id``, so a page boundary inside a run of equal sort keys neither repeats nor
skips a row. Filtering on the sort key alone drops the rest of such a run,
which is why every cursor carries the id and every listing compares it.

Every repository builds its keyset predicate here, so the comparison is spelled
once (``tests/arch/test_keyset_helper.py`` rejects hand-written copies). It is a
row-value comparison, ``(sort, id) < (:s, :id)`` (``>`` for ascending order):
PostgreSQL can use it as a single range condition on a ``(sort, id)`` index, and
SQLite (3.15+) evaluates it with the same lexicographic semantics. Both columns
must be ordered in the same direction for the comparison to match the
``ORDER BY``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, SQLColumnExpression, tuple_
from sqlalchemy.sql.elements import ColumnElement

__all__ = [
    "apply_asc_keyset",
    "apply_desc_keyset",
    "asc_keyset_predicate",
    "desc_keyset_predicate",
]


def desc_keyset_predicate(
    sort_col: SQLColumnExpression[Any],
    id_col: SQLColumnExpression[Any],
    sort_value: object,
    id_value: object,
) -> ColumnElement[bool]:
    """Rows strictly after ``(sort_value, id_value)`` in ``(sort DESC, id DESC)`` order."""
    return tuple_(sort_col, id_col) < tuple_(sort_value, id_value, types=_types(sort_col, id_col))


def asc_keyset_predicate(
    sort_col: SQLColumnExpression[Any],
    id_col: SQLColumnExpression[Any],
    sort_value: object,
    id_value: object,
) -> ColumnElement[bool]:
    """Rows strictly after ``(sort_value, id_value)`` in ``(sort ASC, id ASC)`` order."""
    return tuple_(sort_col, id_col) > tuple_(sort_value, id_value, types=_types(sort_col, id_col))


def apply_desc_keyset[SelectT: Select[Any]](
    stmt: SelectT,
    sort_col: SQLColumnExpression[Any],
    id_col: SQLColumnExpression[Any],
    cursor: tuple[object, object] | None,
) -> SelectT:
    """Narrow ``stmt`` to rows after ``cursor`` in descending order; no-op when ``None``."""
    if cursor is None:
        return stmt
    sort_value, id_value = cursor
    return stmt.where(desc_keyset_predicate(sort_col, id_col, sort_value, id_value))


def apply_asc_keyset[SelectT: Select[Any]](
    stmt: SelectT,
    sort_col: SQLColumnExpression[Any],
    id_col: SQLColumnExpression[Any],
    cursor: tuple[object, object] | None,
) -> SelectT:
    """Narrow ``stmt`` to rows after ``cursor`` in ascending order; no-op when ``None``."""
    if cursor is None:
        return stmt
    sort_value, id_value = cursor
    return stmt.where(asc_keyset_predicate(sort_col, id_col, sort_value, id_value))


def _types(*cols: SQLColumnExpression[Any]) -> list[Any]:
    """Bind each cursor value with its column's type (e.g. tz-aware ``DateTime`` on SQLite)."""
    return [col.type for col in cols]  # type: ignore[attr-defined]
