"""Keyset (seek) pagination predicates over a ``(sort key, id)`` pair.

A listing ordered by ``(sort_col, id_col)`` resumes after the last row it
returned by comparing both columns: rows that share the sort key are ordered by
``id``, so a page boundary inside a run of equal sort keys neither repeats nor
skips a row. Filtering on the sort key alone drops the rest of such a run,
which is why every cursor carries the id and every listing compares it.

The predicates expand to ``sort < :s OR (sort = :s AND id < :id)`` (or ``>``
for ascending order) rather than a row-value comparison, so the same SQL runs
on PostgreSQL and SQLite and stays index-friendly.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, SQLColumnExpression, and_, or_
from sqlalchemy.sql.elements import ColumnElement

__all__ = [
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
    return or_(sort_col < sort_value, and_(sort_col == sort_value, id_col < id_value))


def asc_keyset_predicate(
    sort_col: SQLColumnExpression[Any],
    id_col: SQLColumnExpression[Any],
    sort_value: object,
    id_value: object,
) -> ColumnElement[bool]:
    """Rows strictly after ``(sort_value, id_value)`` in ``(sort ASC, id ASC)`` order."""
    return or_(sort_col > sort_value, and_(sort_col == sort_value, id_col > id_value))


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
