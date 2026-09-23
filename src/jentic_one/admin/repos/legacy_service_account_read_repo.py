"""Read-only raw-SQL access to the retired ``service_accounts`` table (theme 8).

Theme-8 Phase 2 removed the service-account surface (router, services, ORM
repositories). What remains is the Phase-1 resolver fallback: an unmigrated
``sak_`` / ``jntc_live_`` key can still resolve to an ``sva_`` identity until
the Phase-4 drop, and ``GET /me`` / the MCP ``me`` tool must answer coherently
for it. This module is that one read — raw SQL, mirroring the broker
``token_resolver`` CASE-arm style, so nothing re-grows a dependency on the
deleted ORM repositories. Deleted with the tables in Phase 4.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.shared.models import ActorType

_GET_SERVICE_ACCOUNT = text(
    "SELECT id, name, status, registered_by, approved_by"
    " FROM service_accounts WHERE id = :service_account_id"
)

_LIST_SERVICE_ACCOUNT_PERMISSIONS = text(
    "SELECT permission FROM actor_permission_grants"
    " WHERE actor_id = :service_account_id AND actor_type = :actor_type"
    " ORDER BY permission"
)


class LegacyServiceAccountReadRepository:
    """Self-identity reads for fallback-resolved ``sva_`` callers — read-only."""

    @staticmethod
    async def get_identity(session: AsyncSession, service_account_id: str) -> Any | None:
        """Return ``(id, name, status, registered_by, approved_by)`` or ``None``."""
        result = await session.execute(
            _GET_SERVICE_ACCOUNT, {"service_account_id": service_account_id}
        )
        return result.first()

    @staticmethod
    async def list_permissions(session: AsyncSession, service_account_id: str) -> list[str]:
        """Return the account's live ``actor_permission_grants`` permissions."""
        result = await session.execute(
            _LIST_SERVICE_ACCOUNT_PERMISSIONS,
            {
                "service_account_id": service_account_id,
                "actor_type": ActorType.SERVICE_ACCOUNT.value,
            },
        )
        return [row.permission for row in result]
