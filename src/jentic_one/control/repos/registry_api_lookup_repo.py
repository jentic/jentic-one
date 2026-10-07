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

_IDENTITIES_FOR_VENDOR = text(
    "SELECT DISTINCT name, version FROM apis WHERE vendor = :vendor ORDER BY name, version"
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
