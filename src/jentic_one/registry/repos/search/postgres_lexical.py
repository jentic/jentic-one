"""PostgreSQL full-text (BM25-style) lexical search strategy.

Uses PostgreSQL's native full-text search: ``to_tsvector`` over the operation's
``search_text`` matched against ``websearch_to_tsquery`` and ranked with
``ts_rank_cd``. Applies the same revision-pin and published-state filters as the
other strategies and paginates with a keyset cursor on ``(distance, id)``.

``ts_rank_cd`` is unbounded and always non-negative. We squash it into ``[0, 1)``
via ``rank / (rank + 1)`` and expose ``distance = 1 - squashed = 1 / (rank + 1)``
so the strategy keeps the ascending-distance contract (smaller = better) and can
never emit a negative distance that would corrupt keyset pagination.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Float, cast, func, literal, literal_column, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.repos.search.protocol import SearchCursor, SearchHit
from jentic_one.registry.repos.search.registry import register_strategy
from jentic_one.shared.models import ApiRevisionState

# The text-search configuration must reach Postgres as a ``regconfig``, not a
# bound VARCHAR: ``to_tsvector`` / ``websearch_to_tsquery`` only overload on
# ``(regconfig, text)``. A bound ``literal("english")`` renders as ``$n::VARCHAR``
# and fails with ``function to_tsvector(character varying, text) does not exist``.
# Render it inline as a regconfig literal instead. The value is a fixed constant
# (never user input), so inlining is safe from injection.
_TS_CONFIG: ColumnElement[str] = literal_column("'english'::regconfig")


@register_strategy
class PostgresLexicalStrategy:
    """Lexical full-text search over operation text using Postgres tsvector/ts_rank_cd."""

    name = "lexical"
    dialect = "postgres"

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
        document = func.to_tsvector(_TS_CONFIG, func.coalesce(Operation.search_text, ""))
        tsquery = func.websearch_to_tsquery(_TS_CONFIG, query)
        rank = func.ts_rank_cd(document, tsquery)
        # Squash unbounded rank into (0, 1] distance (see module docstring).
        distance = cast(1.0 / (rank + 1.0), Float).label("distance")

        stmt = (
            select(
                Operation.id,
                Operation.revision_id,
                ApiRevision.api_id,
                distance,
            )
            .join(ApiRevision, ApiRevision.id == Operation.revision_id)
            .where(document.op("@@")(tsquery))
        )

        pins = revision_pins or {}
        if pins:
            stmt = stmt.where(
                (
                    ApiRevision.api_id.notin_(list(pins.keys()))
                    & (ApiRevision.state == ApiRevisionState.PUBLISHED)
                )
                | ApiRevision.id.in_(list(pins.values()))
            )
        else:
            stmt = stmt.where(ApiRevision.state == ApiRevisionState.PUBLISHED)

        if api_filters:
            stmt = stmt.where(ApiRevision.api_id.in_(api_filters))

        if cursor is not None:
            stmt = stmt.where(
                tuple_(distance, Operation.id)
                > tuple_(literal(cursor.distance), literal(cursor.operation_id))
            )

        stmt = stmt.order_by(distance.asc(), Operation.id.asc()).limit(limit)

        result = await session.execute(stmt)
        return [
            SearchHit(
                operation_id=row.id,
                revision_id=row.revision_id,
                api_id=row.api_id,
                distance=float(row.distance),
            )
            for row in result.all()
        ]
