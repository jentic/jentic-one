"""Regression tests for the Postgres lexical search strategy's SQL.

Guards the FTS-config type bug: ``to_tsvector`` / ``websearch_to_tsquery`` only
overload on ``(regconfig, text)`` in Postgres. Rendering the config as a bound
``VARCHAR`` (``literal("english")``) produces ``to_tsvector($n::VARCHAR, ...)``,
which fails at runtime with:

    function to_tsvector(character varying, text) does not exist

so `jentic search` 500s. The strategy must render the config inline as
``'english'::regconfig``. These tests compile the exact constructs the strategy
uses (no live DB needed) and assert the rendered SQL is the regconfig form.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql

from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.repos.search import postgres_lexical


def _rendered_fts_sql() -> str:
    cfg = postgres_lexical._TS_CONFIG
    document = func.to_tsvector(cfg, func.coalesce(Operation.search_text, ""))
    tsquery = func.websearch_to_tsquery(cfg, "spreadsheet values")
    stmt = select(Operation.id).where(document.op("@@")(tsquery))
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_fts_config_rendered_as_regconfig() -> None:
    sql = _rendered_fts_sql()
    assert "'english'::regconfig" in sql, sql


def test_fts_config_not_bound_as_varchar() -> None:
    # The exact failure signature: a VARCHAR-typed config argument. If the config
    # is ever bound as a plain param again, this catches it before runtime.
    sql = _rendered_fts_sql().lower()
    assert "::varchar" not in sql.split("to_tsvector", 1)[1].split(")", 1)[0], sql


def test_uses_both_fts_functions_with_regconfig() -> None:
    sql = _rendered_fts_sql()
    assert "to_tsvector('english'::regconfig" in sql, sql
    assert "websearch_to_tsquery('english'::regconfig" in sql, sql
