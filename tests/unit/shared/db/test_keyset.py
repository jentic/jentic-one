"""Tests for the shared ``(sort key, id)`` keyset predicates."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Column, DateTime, MetaData, String, Table, select
from sqlalchemy.dialects.postgresql.base import PGDialect
from sqlalchemy.dialects.sqlite.base import SQLiteDialect
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.sql.elements import ClauseElement

from jentic_one.shared.db.keyset import (
    apply_desc_keyset,
    asc_keyset_predicate,
    desc_keyset_predicate,
)

_ROWS = Table(
    "rows",
    MetaData(),
    Column("id", String, primary_key=True),
    Column("created_at", DateTime(timezone=True)),
)
_TS = datetime(2026, 1, 1, tzinfo=UTC)


def _sql(clause: ClauseElement, dialect: Dialect) -> str:
    return str(clause.compile(dialect=dialect)).replace("\n", " ")


_DIALECTS: list[Dialect] = [PGDialect(), SQLiteDialect()]  # type: ignore[no-untyped-call]


@pytest.mark.parametrize("dialect", _DIALECTS, ids=["pg", "sqlite"])
def test_desc_predicate_breaks_timestamp_ties_on_id(dialect: Dialect) -> None:
    """``sort < :s OR (sort = :s AND id < :id)`` — the id decides within a tied timestamp."""
    sql = _sql(desc_keyset_predicate(_ROWS.c.created_at, _ROWS.c.id, _TS, "row_b"), dialect)
    assert "rows.created_at <" in sql
    assert "rows.created_at =" in sql
    assert "rows.id <" in sql
    assert " OR " in sql
    assert " AND " in sql


def test_asc_predicate_compares_greater_than() -> None:
    sql = _sql(asc_keyset_predicate(_ROWS.c.created_at, _ROWS.c.id, _TS, "row_b"), SQLiteDialect())
    assert "rows.created_at >" in sql
    assert "rows.id >" in sql
    assert "<" not in sql


def test_apply_desc_keyset_without_cursor_leaves_statement_unchanged() -> None:
    stmt = select(_ROWS)
    assert apply_desc_keyset(stmt, _ROWS.c.created_at, _ROWS.c.id, None) is stmt


def test_apply_desc_keyset_with_cursor_adds_where_clause() -> None:
    stmt = apply_desc_keyset(select(_ROWS), _ROWS.c.created_at, _ROWS.c.id, (_TS, "row_b"))
    sql = _sql(stmt, SQLiteDialect())
    assert "WHERE rows.created_at <" in sql
    assert "rows.id <" in sql
