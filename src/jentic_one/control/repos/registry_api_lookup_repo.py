"""Cross-DB read: does a credential's API scope cover any registry API identity?

Credential resolution keys on the *workspace* API identity, but credential
creation accepts an unvalidated reference — a scope that matches no imported
API only surfaces at execute time as an opaque 403 ``no_toolkit_binding``
(#1020). This repository answers the coverage question at create time so the
service can attach an advisory warning.

Control may not import registry ORM (arch boundary: no cross-imports between
broker/control/admin/registry), so — mirroring the broker's
``toolkit_binding_resolver`` — this runs raw SQL over the registry DB through
the shared ``DatabaseSession``. Only plain comparisons are emitted, so the
statements are dialect-portable across Postgres and SQLite.
"""

from __future__ import annotations

from sqlalchemy import TextClause, text
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.shared.models.api_identity import CredentialScope


def _scope_query(*, name_scoped: bool, version_scoped: bool) -> TextClause:
    """Coverage probe with a NULL (wildcard) scope axis dropped from the WHERE.

    The inverse of ``credential_coverage_where``: there the *credential* rows
    carry the NULL wildcards and the probe identity is concrete; here the
    scope's non-NULL axes constrain the concrete ``apis`` rows.
    """
    clauses = ["vendor = :vendor"]
    if name_scoped:
        clauses.append("name = :name")
    if version_scoped:
        clauses.append("version = :version")
    return text("SELECT 1 FROM apis WHERE " + " AND ".join(clauses) + " LIMIT 1")


_IDENTITIES_FOR_VENDOR = text(
    "SELECT DISTINCT name, version FROM apis WHERE vendor = :vendor "
    "ORDER BY name, version LIMIT :limit"
)


class RegistryApiLookupRepository:
    """Read-only raw-SQL lookups against the registry ``apis`` table."""

    @staticmethod
    async def scope_covers_any(session: AsyncSession, scope: CredentialScope) -> bool:
        """True when at least one registry API identity is covered by ``scope``.

        ``scope`` is assumed canonical (``canonical_credential_scope``) — the
        registry stores slugified vendor/name, so the comparison is exact.
        """
        params: dict[str, str] = {"vendor": scope.vendor}
        if scope.name is not None:
            params["name"] = scope.name
        if scope.version is not None:
            params["version"] = scope.version
        stmt = _scope_query(
            name_scoped=scope.name is not None, version_scoped=scope.version is not None
        )
        row = (await session.execute(stmt, params)).first()
        return row is not None

    @staticmethod
    async def identities_for_vendor(
        session: AsyncSession, vendor: str, *, limit: int = 5
    ) -> list[tuple[str, str]]:
        """Distinct ``(name, version)`` identities registered under ``vendor``.

        Used for the nearest-identity hint when a scope covers nothing: same
        vendor, different name/version is exactly the #1020 dead-end shape.
        """
        rows = (
            await session.execute(_IDENTITIES_FOR_VENDOR, {"vendor": vendor, "limit": limit})
        ).all()
        return [(row.name, row.version) for row in rows]
