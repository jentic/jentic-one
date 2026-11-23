"""Repository for Operation entities."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import cast

import structlog
from sqlalchemy import String, TableClause, bindparam, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.shared.db.keyset import apply_asc_keyset
from jentic_one.shared.models import ApiRevisionState
from jentic_one.shared.pagination import Cursor

logger = structlog.get_logger()


@dataclass
class OperationInput:
    """ORM-free input for creating operations."""

    path: str
    method: str
    operation_id: str | None = None
    summary: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    deprecated: bool = False
    raw_operation: dict | None = None  # type: ignore[type-arg]


def _generate_operation_id(revision_id: uuid.UUID, path: str, method: str) -> str:
    key = f"{revision_id}:{path}:{method}"
    digest = hashlib.sha256(key.encode()).hexdigest()[:40]
    return f"op_{digest}"[:50]


_SPEC_OPERATION_ID_MAX_LEN = cast(String, Operation.__table__.c.operation_id.type).length or 255


def _assign_spec_operation_ids(
    revision_id: uuid.UUID, operations: list[OperationInput]
) -> list[str | None]:
    """Return a storable spec ``operationId`` per input, in input order.

    Duplicate operationIds are invalid OpenAPI but common in real specs, and
    ``uq_operations_revision_operation_id`` plus the column length would reject
    the whole import (#1437). Over-long ids are truncated to the column length;
    the first holder of an id keeps it and each later duplicate becomes
    ``<id>_<n>`` (``n`` from 2, skipping any id the spec itself declares) so
    every operation stays addressable by a deterministic operationId.

    Blank (empty or whitespace-only) and non-string operationIds are stored as
    NULL: the unique constraint ignores NULLs but not repeated ``""`` values,
    and malformed specs routinely emit ``"operationId": ""`` or a number.
    """
    raw_ids: list[object] = [op.operation_id for op in operations]
    truncated = [
        op_id[:_SPEC_OPERATION_ID_MAX_LEN] if isinstance(op_id, str) and op_id.strip() else None
        for op_id in raw_ids
    ]
    declared = {op_id for op_id in truncated if op_id is not None}
    used: set[str] = set()
    assigned: list[str | None] = []
    for op_input, raw_id, candidate in zip(operations, raw_ids, truncated, strict=True):
        if candidate is None:
            assigned.append(None)
            if raw_id is not None:
                logger.warning(
                    "spec_operation_id_coerced",
                    revision_id=str(revision_id),
                    path=op_input.path,
                    method=op_input.method,
                    reasons=["blank" if isinstance(raw_id, str) else "not_string"],
                    original_type=type(raw_id).__name__,
                    stored_operation_id=None,
                )
            continue
        reasons: list[str] = []
        if candidate != raw_id:
            reasons.append("too_long")
        if candidate in used:
            reasons.append("duplicate")
            n = 2
            while True:
                suffix = f"_{n}"
                renamed = candidate[: _SPEC_OPERATION_ID_MAX_LEN - len(suffix)] + suffix
                if renamed not in used and renamed not in declared:
                    break
                n += 1
            candidate = renamed
        used.add(candidate)
        assigned.append(candidate)
        if reasons:
            logger.warning(
                "spec_operation_id_coerced",
                revision_id=str(revision_id),
                path=op_input.path,
                method=op_input.method,
                reasons=reasons,
                original_operation_id=cast(str, raw_id)[:_SPEC_OPERATION_ID_MAX_LEN],
                original_length=len(cast(str, raw_id)),
                stored_operation_id=candidate,
            )
    return assigned


class OperationRepository:
    """Data access layer for Operation entities — flush-only, never commits."""

    @staticmethod
    async def delete_for_revision(session: AsyncSession, revision_id: uuid.UUID) -> None:
        await session.execute(delete(Operation).where(Operation.revision_id == revision_id))
        await session.flush()

    @staticmethod
    async def bulk_create(
        session: AsyncSession,
        revision_id: uuid.UUID,
        operations: list[OperationInput],
        *,
        created_by: str,
    ) -> list[str]:
        # Last-wins dedup on (path, method) to avoid IntegrityError on in-batch duplicates
        deduped: dict[tuple[str, str], OperationInput] = {}
        for op_input in operations:
            deduped[(op_input.path, op_input.method)] = op_input

        unique_inputs = list(deduped.values())
        spec_operation_ids = _assign_spec_operation_ids(revision_id, unique_inputs)

        ids: list[str] = []
        for op_input, spec_operation_id in zip(unique_inputs, spec_operation_ids, strict=True):
            op_id = _generate_operation_id(revision_id, op_input.path, op_input.method)
            summary = op_input.summary[:500] if op_input.summary else None
            operation = Operation(
                id=op_id,
                revision_id=revision_id,
                operation_id=spec_operation_id,
                path=op_input.path,
                method=op_input.method,
                summary=summary,
                description=op_input.description,
                tags=op_input.tags,
                deprecated=op_input.deprecated,
                raw_operation=op_input.raw_operation,
                created_by=created_by,
            )
            session.add(operation)
            ids.append(op_id)
        await session.flush()
        return ids

    @staticmethod
    async def get_by_ids(session: AsyncSession, ids: set[str]) -> list[Operation]:
        result = await session.execute(select(Operation).where(Operation.id.in_(ids)))
        return list(result.unique().scalars().all())

    @staticmethod
    async def list_for_revision(
        session: AsyncSession, *, revision_id: uuid.UUID
    ) -> list[Operation]:
        """Every operation of a revision (servers eager-loaded via the relationships)."""
        result = await session.execute(
            select(Operation)
            .where(Operation.revision_id == revision_id)
            .order_by(Operation.path.asc(), Operation.method.asc())
        )
        return list(result.unique().scalars().all())

    @staticmethod
    async def list_page_for_revision(
        session: AsyncSession,
        *,
        revision_id: uuid.UUID,
        limit: int = 50,
        cursor: Cursor | None = None,
    ) -> list[Operation]:
        stmt = (
            select(Operation)
            .where(Operation.revision_id == revision_id)
            .order_by(Operation.created_at.asc(), Operation.id.asc())
            .limit(limit)
        )
        stmt = apply_asc_keyset(stmt, Operation.created_at, Operation.id, cursor)
        result = await session.execute(stmt)
        return list(result.unique().scalars().all())

    @staticmethod
    async def get_by_id_for_inspect(session: AsyncSession, operation_id: str) -> Operation | None:
        """Load an operation with its revision and API for inspect resolution.

        Primary lookup is by the registry primary key (``op_…``). As a
        convenience fallback — so an agent can pass the natural-looking spec
        ``operationId`` surfaced by ``catalog show`` — when the value is not a
        registry key we also match the ``Operation.operation_id`` spec column,
        preferring the API's current revision and otherwise the most recently
        created revision. See issue #670 (id-namespace collision).
        """
        stmt = (
            select(Operation)
            .where(Operation.id == operation_id)
            .options(joinedload(Operation.revision).joinedload(ApiRevision.api))
        )
        result = await session.execute(stmt)
        operation = result.unique().scalar_one_or_none()
        if operation is not None:
            return operation

        # Fallback: treat the value as a spec operationId. Registry keys are
        # always ``op_…``; anything else can only be a spec operationId.
        if operation_id.startswith("op_"):
            return None
        return await OperationRepository._get_by_spec_operation_id(session, operation_id)

    @staticmethod
    async def _get_by_spec_operation_id(
        session: AsyncSession, spec_operation_id: str
    ) -> Operation | None:
        """Resolve a spec ``operationId`` to a single operation, unambiguously.

        Only operations on a *live* revision (``PUBLISHED``/``IMPORTED``) are
        considered, mirroring ``/search`` so this convenience lookup can't
        enumerate a draft or archived operation by a guessable spec id.

        A spec ``operationId`` is unique only *within* an API, so a value that
        matches operations across more than one API is genuinely ambiguous: we
        resolve nothing (the caller 404s and the agent is steered to the registry
        ``operation_id``) rather than silently returning an arbitrary vendor's
        operation. Within a single API we prefer the current revision, then the
        most recently created. See issue #670 (id-namespace collision).
        """
        live_states = (ApiRevisionState.PUBLISHED, ApiRevisionState.IMPORTED)
        stmt = (
            select(Operation)
            .join(ApiRevision, Operation.revision_id == ApiRevision.id)
            .join(Api, ApiRevision.api_id == Api.id)
            .where(
                Operation.operation_id == spec_operation_id,
                ApiRevision.state.in_(live_states),
            )
            .options(joinedload(Operation.revision).joinedload(ApiRevision.api))
            .order_by(
                (Api.current_revision_id == Operation.revision_id).desc(),
                ApiRevision.created_at.desc(),
                Operation.id,
            )
        )
        result = await session.execute(stmt)
        matches = result.unique().scalars().all()
        if not matches:
            return None
        if len({op.revision.api_id for op in matches}) > 1:
            return None
        return matches[0]

    @staticmethod
    async def set_search_text(session: AsyncSession, id_to_text: dict[str, str]) -> None:
        """Bulk-update the lexical search text for operations.

        Issues a single parameterized UPDATE (executemany) keyed by operation id.
        On SQLite the ``operations`` AFTER UPDATE trigger keeps ``operations_fts``
        synchronized; on PostgreSQL the GIN expression index is maintained
        automatically.
        """
        if not id_to_text:
            return
        table = cast(TableClause, Operation.__table__)
        stmt = (
            update(table)
            .where(table.c.id == bindparam("op_id"))
            .values(search_text=bindparam("text_value"))
        )
        params = [
            {"op_id": op_id, "text_value": text_value} for op_id, text_value in id_to_text.items()
        ]
        await session.execute(stmt, params)
        await session.flush()
