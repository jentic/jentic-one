"""Cross-database cleanup of control credentials when a registry API is deleted.

Registry and Control are **separate databases** with no referential integrity
between ``apis`` and ``credentials``. When an
API is deleted from the registry, the control-plane credentials that reference it
by ``(api_vendor, api_name, api_version)`` are left active — a later re-import
plus a new credential then collides with ``409 ambiguous_credential`` (issue
#643).

This repository deactivates those stranded credentials; the agent bindings in
the admin database are suspended by ``admin_credential_binding_boundary_repo.py``
(#1168). (It also removed the legacy toolkit bindings to them until theme-5
Phase 6b dropped the toolkit tables.) It uses raw SQL
(``text()``) so the registry module never imports control ORM models — the same
boundary pattern as ``control/repos/prerequisite_repo.py`` reading the admin DB.
It is a registry ``repos/`` file, so it is exempt from the no-direct-DB rule and
runs against a **control-database** session handed in by the caller.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Every credential stored for an exact ``(vendor, name, version)`` identity,
# active or not. Deliberately NOT filtered on ``active``: an already-inactive
# credential can be re-activated later, and its agent bindings must not come
# back with it when the API it served has been deleted.
_CREDENTIAL_IDS_FOR_API = text(
    "SELECT id FROM credentials "
    "WHERE api_vendor = :api_vendor AND api_name = :api_name AND api_version = :api_version "
    "ORDER BY id"
)


class ControlCredentialBoundaryRepository:
    """Deactivates control credentials stranded by a registry API delete.

    Runs against a control-DB session (no control ORM imports). ``api_name`` /
    ``api_version`` are matched exactly when given; a ``None`` component matches
    any value, mirroring the broker resolver's identity matching.
    """

    @staticmethod
    async def credential_ids_for_api(
        session: AsyncSession, *, api_vendor: str, api_name: str, api_version: str
    ) -> list[str]:
        """Ids of every credential stored for the exact API identity (any ``active``).

        Wildcard-scoped credentials (a ``NULL`` name or version) are not
        returned: they cover other APIs of the vendor as well, so deleting one
        API does not retire them.
        """
        rows = (
            await session.execute(
                _CREDENTIAL_IDS_FOR_API,
                {"api_vendor": api_vendor, "api_name": api_name, "api_version": api_version},
            )
        ).all()
        return [row[0] for row in rows]

    @staticmethod
    async def deactivate_credentials_for_api(
        session: AsyncSession,
        *,
        api_vendor: str,
        api_name: str | None,
        api_version: str | None,
    ) -> int:
        """Mark matching active credentials inactive; return the number changed.

        Marking inactive (rather than deleting) preserves the row — the operator
        can still see and rotate it — while removing it from the broker
        resolver's active-match set so a re-import can't collide with it. The
        agent bindings to the credentials are suspended separately (admin DB,
        ``admin_credential_binding_boundary_repo.py``).
        """
        # CAST the nullable parameters to VARCHAR so Postgres (asyncpg) can
        # determine their type: a bare bind parameter used only in an
        # ``:param IS NULL`` test has no inferrable type and asyncpg raises
        # "could not determine data type of parameter". Standard
        # ``CAST(x AS VARCHAR)`` (not Postgres-specific ``::text``) keeps the
        # same statement working on both Postgres and SQLite.
        result = await session.execute(
            text(
                "UPDATE credentials SET active = false "
                "WHERE active = true "
                "AND api_vendor = :api_vendor "
                "AND (CAST(:api_name AS VARCHAR) IS NULL OR api_name = :api_name) "
                "AND (CAST(:api_version AS VARCHAR) IS NULL OR api_version = :api_version)"
            ),
            {
                "api_vendor": api_vendor,
                "api_name": api_name,
                "api_version": api_version,
            },
        )
        return int(result.rowcount)  # type: ignore[attr-defined]
