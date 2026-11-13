"""Cross-DB read: which registry API identities exist under a credential's vendor?

Credential resolution keys on the *workspace* API identity, but credential
creation accepts an unvalidated reference — a scope that matches no imported
API only surfaces at execute time as an opaque 403 ``no_toolkit_binding``
(#1020). This repository feeds the create-time coverage check so the service
can attach an advisory warning.

Control may not import registry ORM (arch boundary: no cross-imports between
broker/control/admin/registry), so — mirroring the broker's
``toolkit_binding_resolver`` — this runs raw SQL over the registry DB through
the shared ``DatabaseSession``. Only plain comparisons are emitted, so the
statement is dialect-portable across Postgres and SQLite.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.shared.permissions.operation_resolution import LiveOperationRow

_IDENTITIES_FOR_VENDOR = text(
    "SELECT DISTINCT name, version FROM apis WHERE vendor = :vendor ORDER BY name, version"
)
_IDENTITY_FOR_CATALOG_API_ID = text(
    "SELECT vendor, name FROM apis WHERE catalog_api_id = :catalog_api_id "
    "ORDER BY created_at, id LIMIT 1"
)
_LIVE_OPERATIONS_FOR_VENDOR = text(
    "SELECT a.vendor, a.name, a.version, o.id, o.operation_id, o.method, o.path "
    "FROM apis a "
    "JOIN api_revisions r ON r.id = a.current_revision_id "
    "JOIN operations o ON o.revision_id = r.id "
    "WHERE a.vendor = :vendor AND r.state IN ('published', 'imported') "
    "ORDER BY a.name, a.version, o.path, o.method"
)


class RegistryApiLookupRepository:
    """Read-only raw-SQL lookups against the registry ``apis`` table."""

    @staticmethod
    async def identities_for_vendor(session: AsyncSession, vendor: str) -> list[tuple[str, str]]:
        """Distinct ``(name, version)`` identities registered under ``vendor``, sorted.

        One round-trip answers both questions the create-time check asks: whether
        the scope covers any of them (evaluated by the caller with the shared
        ``credential_covers`` semantics) and, when it covers none, which same-vendor
        identities to name in the hint — exactly the #1020 dead-end shape. The
        registry stores slugified vendor/name, so ``vendor`` must be canonical.
        """
        rows = (await session.execute(_IDENTITIES_FOR_VENDOR, {"vendor": vendor})).all()
        return [(row.name, row.version) for row in rows]

    @staticmethod
    async def identity_for_catalog_api_id(
        session: AsyncSession, catalog_api_id: str
    ) -> tuple[str, str] | None:
        """The ``(vendor, name)`` the local API imported from ``catalog_api_id`` carries.

        The oldest row wins, so the answer is deterministic when several versions
        share the catalog id; ``None`` when nothing was imported from it.
        """
        row = (
            await session.execute(_IDENTITY_FOR_CATALOG_API_ID, {"catalog_api_id": catalog_api_id})
        ).first()
        return (row.vendor, row.name) if row is not None else None

    @staticmethod
    async def live_operations_for_vendor(
        session: AsyncSession, vendor: str
    ) -> list[LiveOperationRow]:
        """Every operation belonging to a live revision under ``vendor``."""
        rows = (await session.execute(_LIVE_OPERATIONS_FOR_VENDOR, {"vendor": vendor})).all()
        return [
            LiveOperationRow(
                api_vendor=str(row[0]),
                api_name=str(row[1]),
                api_version=str(row[2]),
                id=str(row[3]),
                operation_id=str(row[4]) if row[4] is not None else None,
                method=str(row[5]),
                path=str(row[6]),
            )
            for row in rows
        ]
