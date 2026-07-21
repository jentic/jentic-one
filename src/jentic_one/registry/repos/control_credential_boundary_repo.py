"""Cross-database cleanup of control credentials when a registry API is deleted.

Registry and Control are **separate databases** with no referential integrity
between ``apis`` and ``credentials`` / ``toolkit_credential_bindings``. When an
API is deleted from the registry, the control-plane credentials that reference it
by ``(api_vendor, api_name, api_version)`` are left active — a later re-import
plus a new credential then collides with ``409 ambiguous_credential`` (issue
#643).

This repository deactivates those stranded credentials. It uses raw SQL
(``text()``) so the registry module never imports control ORM models — the same
boundary pattern as ``control/repos/prerequisite_repo.py`` reading the admin DB.
It is a registry ``repos/`` file, so it is exempt from the no-direct-DB rule and
runs against a **control-database** session handed in by the caller.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class ControlCredentialBoundaryRepository:
    """Deactivates control credentials stranded by a registry API delete.

    Runs against a control-DB session (no control ORM imports). ``api_name`` /
    ``api_version`` are matched exactly when given; a ``None`` component matches
    any value, mirroring the broker resolver's identity matching.
    """

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
        ``toolkit_credential_bindings`` rows survive (they cascade only on
        credential *deletion*); the deactivated credential simply stops resolving.
        """
        result = await session.execute(
            text(
                "UPDATE credentials SET active = false "
                "WHERE active = true "
                "AND api_vendor = :api_vendor "
                "AND (:api_name IS NULL OR api_name = :api_name) "
                "AND (:api_version IS NULL OR api_version = :api_version)"
            ),
            {
                "api_vendor": api_vendor,
                "api_name": api_name,
                "api_version": api_version,
            },
        )
        return int(result.rowcount)  # type: ignore[attr-defined]
