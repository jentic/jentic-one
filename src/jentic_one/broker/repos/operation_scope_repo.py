"""Registry-DB read helpers for broker operation-rule identity enforcement.

The broker cannot import ``jentic_one.registry`` ORM models, so cross-API
``operationId`` collision checks for wildcard credentials and primary-key
``operationId`` lookups for legacy queued job payloads run as dialect-portable
SQL over ``ctx.registry_db``.
"""

from __future__ import annotations

from sqlalchemy import text

from jentic_one.shared.db import DatabaseSession

_LIVE_APIS_WITH_SPEC_OP_ID = text(
    "SELECT a.name, a.version "
    "FROM apis a "
    "JOIN api_revisions r ON r.id = a.current_revision_id "
    "JOIN operations o ON o.revision_id = r.id "
    "WHERE a.vendor = :vendor "
    "  AND r.state IN ('published', 'imported') "
    "  AND o.operation_id = :operation_id"
)

_SPEC_OP_ID_BY_PK = text("SELECT operation_id FROM operations WHERE id = :id")


class OperationScopeRepository:
    """Read-only raw-SQL lookups against the registry ``apis``/``operations`` tables."""

    @staticmethod
    async def live_apis_declaring_operation_id(
        registry_db: DatabaseSession, *, vendor: str, spec_operation_id: str
    ) -> list[tuple[str, str]]:
        """Return ``(name, version)`` of live APIs under ``vendor`` with ``spec_operation_id``."""
        async with registry_db.read_session() as session:
            rows = (
                await session.execute(
                    _LIVE_APIS_WITH_SPEC_OP_ID,
                    {"vendor": vendor, "operation_id": spec_operation_id},
                )
            ).all()
        return [(str(row[0]), str(row[1])) for row in rows]

    @staticmethod
    async def spec_operation_id_for_pk(
        registry_db: DatabaseSession, *, operation_pk: str
    ) -> str | None:
        """Return ``Operation.operation_id`` for ``Operation.id``, or ``None``."""
        async with registry_db.read_session() as session:
            row = (await session.execute(_SPEC_OP_ID_BY_PK, {"id": operation_pk})).first()
        if row is None or row[0] is None:
            return None
        return str(row[0])
