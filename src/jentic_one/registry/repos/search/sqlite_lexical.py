"""SQLite FTS5 (BM25) lexical search strategy.

Ranks operations by SQLite's built-in ``bm25()`` over the ``operations_fts``
virtual table, joins back to ``operations``/``api_revisions`` to apply the same
revision-pin and active-state (published or imported) filters as every other
strategy, and returns distance-ordered hits with keyset pagination.

SQLite's ``bm25()`` returns a value where a *more negative* number is a better
match. We negate it into a non-negative relevance score, squash the unbounded
score into ``[0, 1)`` via ``rel / (rel + 1)``, and expose ``distance = 1 - squashed``
so the strategy keeps the ascending-distance contract (smaller = better) and can
never emit a negative distance that would corrupt keyset pagination.
"""

from __future__ import annotations

import uuid

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.registry.repos.search.protocol import SearchCursor, SearchHit
from jentic_one.registry.repos.search.registry import register_strategy
from jentic_one.shared.models import ApiRevisionState


@register_strategy
class SqliteLexicalStrategy:
    """Lexical BM25 search over operation text for SQLite (FTS5)."""

    name = "lexical"
    dialect = "sqlite"

    async def search_operations(
        self,
        session: AsyncSession,
        *,
        query: str,
        api_filters: list[uuid.UUID] | None = None,
        revision_pins: dict[uuid.UUID, uuid.UUID] | None = None,
        limit: int = 20,
        cursor: SearchCursor | None = None,
    ) -> list[SearchHit]:
        # Squash the unbounded (negated) bm25 relevance into (0, 1] distance:
        #   rel = -bm25(...)              (higher = better, >= 0 for matches)
        #   distance = 1 - rel/(rel + 1) = 1 / (rel + 1)
        distance_expr = "1.0 / ((-bm25(operations_fts)) + 1.0)"

        params: dict[str, object] = {"query": _to_match_query(query), "limit": limit}

        where_clauses = ["operations_fts MATCH :query"]

        # Active revision states a search can surface (mirrors the
        # ``ix_api_revisions_one_active`` index): a catalog import lands as
        # IMPORTED and must be searchable without a manual promote.
        active_binds = []
        for i, state in enumerate((ApiRevisionState.PUBLISHED, ApiRevisionState.IMPORTED)):
            key = f"active_state_{i}"
            active_binds.append(f":{key}")
            params[key] = state.value
        active_clause = "ar.state IN (" + ", ".join(active_binds) + ")"

        pins = revision_pins or {}
        if pins:
            pin_api_binds = []
            for i, api_id in enumerate(pins):
                key = f"pin_api_{i}"
                pin_api_binds.append(f":{key}")
                params[key] = str(api_id)
            pin_rev_binds = []
            for i, rev_id in enumerate(pins.values()):
                key = f"pin_rev_{i}"
                pin_rev_binds.append(f":{key}")
                params[key] = str(rev_id)
            where_clauses.append(
                "((ar.api_id NOT IN (" + ", ".join(pin_api_binds) + ")"
                " AND " + active_clause + ")"
                " OR ar.id IN (" + ", ".join(pin_rev_binds) + "))"
            )
        else:
            where_clauses.append(active_clause)

        if api_filters:
            filter_binds = []
            for i, api_id in enumerate(api_filters):
                key = f"api_filter_{i}"
                filter_binds.append(f":{key}")
                params[key] = str(api_id)
            where_clauses.append("ar.api_id IN (" + ", ".join(filter_binds) + ")")

        if cursor is not None:
            # Keyset on (distance, operation_id): rows strictly after the cursor.
            where_clauses.append(
                f"(({distance_expr} > :cursor_distance)"
                f" OR ({distance_expr} = :cursor_distance AND o.id > :cursor_op_id))"
            )
            params["cursor_distance"] = cursor.distance
            params["cursor_op_id"] = cursor.operation_id

        sql = text(
            f"""
            SELECT
                o.id AS operation_id,
                o.revision_id AS revision_id,
                ar.api_id AS api_id,
                {distance_expr} AS distance
            FROM operations_fts
            JOIN operations o ON o.id = operations_fts.op_id
            JOIN api_revisions ar ON ar.id = o.revision_id
            WHERE {" AND ".join(where_clauses)}
            ORDER BY distance ASC, o.id ASC
            LIMIT :limit
            """
        ).bindparams(*(bindparam(k) for k in params))

        result = await session.execute(sql, params)
        return [
            SearchHit(
                operation_id=row.operation_id,
                revision_id=uuid.UUID(str(row.revision_id)),
                api_id=uuid.UUID(str(row.api_id)),
                distance=float(row.distance),
            )
            for row in result.all()
        ]


def _to_match_query(query: str) -> str:
    """Turn a raw user query into a safe FTS5 MATCH expression.

    Each whitespace-delimited term is double-quoted (so FTS5 treats it as a
    literal phrase token, immune to its query operators) and OR-combined so any
    matching term contributes to the bm25 score. Empty queries fall back to a
    token that matches nothing.
    """
    terms = [t for t in query.replace('"', " ").split() if t]
    if not terms:
        return '""'
    return " OR ".join(f'"{term}"' for term in terms)
