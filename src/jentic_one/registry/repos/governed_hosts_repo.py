"""Cross-database derivation of an identity's governed host set (#1278).

The pipeline mirrors the broker's runtime toolkit derivation
(``broker/repos/toolkit_binding_resolver.py``), run in the opposite direction:
instead of "which toolkits serve *this* API", it derives "which APIs (and so
which hosts) do *this identity's* toolkits cover".

Registry and the admin/control planes are **separate databases** with no
cross-schema referential integrity, and the registry module may import neither
``admin`` nor ``control`` ORM — so the admin/control legs run as raw SQL
(the same boundary pattern as ``control_credential_boundary_repo.py``), against
sessions handed in by the caller. Only the registry leg uses the registry ORM.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, bindparam, select, text
from sqlalchemy import or_ as sql_or
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.shared.models.api_identity import CredentialScope, canonical_credential_scope

# admin DB — the toolkits the identity is bound to (same statement as the
# broker's runtime resolver; agents and other bound actors share the table).
_AGENT_TOOLKITS = text("SELECT toolkit_id FROM agent_toolkit_bindings WHERE agent_id = :agent_id")

# control DB — the distinct stored credential scopes bound to a set of toolkits.
# Active only: an inactive credential no longer resolves at execution time
# (broker coverage matchers filter on ``c.active``), so its hosts are not part
# of the identity's governed set. DISTINCT + ORDER BY for a deterministic scope
# list across backends.
_CREDENTIAL_SCOPES_FOR_TOOLKITS = text(
    "SELECT DISTINCT c.api_vendor, c.api_name, c.api_version "
    "FROM toolkit_credential_bindings tcb "
    "JOIN credentials c ON c.id = tcb.credential_id "
    "WHERE tcb.toolkit_id IN :toolkit_ids AND c.active "
    "ORDER BY c.api_vendor, c.api_name, c.api_version"
).bindparams(bindparam("toolkit_ids", expanding=True))


@dataclass(frozen=True, slots=True)
class GovernedApi:
    """A registered API covered by one of the identity's credential scopes."""

    vendor: str
    name: str
    version: str
    host: str | None


class GovernedHostsRepository:
    """Derives the identity's toolkit → credential-scope → API/host chain.

    Each method runs against the session for **one** database; the caller
    (``GovernedHostsService``) sequences the three legs — the databases are
    separate sessions, so the joins are computed in Python, exactly as the
    broker's ``ToolkitBindingResolver`` does.
    """

    @staticmethod
    async def toolkit_ids_for_identity(session: AsyncSession, *, sub: str) -> set[str]:
        """Toolkit ids bound to the identity (**admin** DB session)."""
        rows = (await session.execute(_AGENT_TOOLKITS, {"agent_id": sub})).all()
        return {row[0] for row in rows}

    @staticmethod
    async def credential_scopes_for_toolkits(
        session: AsyncSession, *, toolkit_ids: set[str]
    ) -> list[CredentialScope]:
        """Distinct active credential scopes bound to the toolkits (**control** DB session).

        Scopes are re-canonicalised on read (slugified vendor/name, empty→``None``)
        so a legacy non-canonical stored row expands against the registry on the
        same footing as the broker's coverage matchers.
        """
        if not toolkit_ids:
            return []
        rows = (
            await session.execute(
                _CREDENTIAL_SCOPES_FOR_TOOLKITS, {"toolkit_ids": sorted(toolkit_ids)}
            )
        ).all()
        # dict.fromkeys: canonicalisation can collapse two stored rows into one
        # scope; preserve the query's deterministic order while deduplicating.
        return list(
            dict.fromkeys(
                canonical_credential_scope(
                    vendor=row.api_vendor, name=row.api_name, version=row.api_version
                )
                for row in rows
            )
        )

    @staticmethod
    async def apis_for_scopes(
        session: AsyncSession, *, scopes: list[CredentialScope]
    ) -> list[GovernedApi]:
        """Registered APIs covered by any of the scopes (**registry** DB session).

        A ``None`` axis on a scope is the wildcard — the comparison for that axis
        is omitted, so a bare-vendor credential expands to every registered API
        of that vendor (the "wildcard-credential expansion" the issue calls for).
        The host is the API's current revision's first API-level server hostname,
        exactly as ``GET /apis`` derives it (``ApiRepository.load_server_hosts``).
        """
        if not scopes:
            return []

        conditions = []
        for scope in scopes:
            axes = [Api.vendor == scope.vendor]
            if scope.name is not None:
                axes.append(Api.name == scope.name)
            if scope.version is not None:
                axes.append(Api.version == scope.version)
            conditions.append(and_(*axes))

        rows = (
            await session.execute(
                select(Api.vendor, Api.name, Api.version, Api.current_revision_id)
                .where(sql_or(*conditions))
                .order_by(Api.vendor, Api.name, Api.version)
            )
        ).all()

        revision_ids = [row.current_revision_id for row in rows if row.current_revision_id]
        hosts = await ApiRepository.load_server_hosts(session, revision_ids)

        return [
            GovernedApi(
                vendor=row.vendor,
                name=row.name,
                version=row.version,
                host=hosts.get(row.current_revision_id) if row.current_revision_id else None,
            )
            for row in rows
        ]
