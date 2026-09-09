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

The registry leg reads ``operation_url_indexes.host`` — the exact host patterns
the broker's discovery matches requests against (``URLLookupService``) — so the
returned set is precisely the hosts this deployment would govern for the
identity: every server (API- and operation-level) of every indexed revision,
default server variables pre-expanded by the index builder. Two deliberate
asymmetries with the raw index, both matching runtime interception:

- **All indexed revisions count, not just the current one.** Discovery's
  ``lookup_by_host_any_revision`` applies no revision or state predicate, and
  archiving a revision clears ``current_revision_id`` without deleting its
  index rows — so an archived revision's hosts still route through the broker
  and must stay in the governed set.
- **Variable-bearing hosts (``{var}`` labels from defaultless server
  variables) are excluded.** The index's regex-match branch requires
  ``host IS NULL``, which the ingest never writes, so a templated host never
  matches a real request — publishing it would tell an integrator's gate to
  divert traffic the broker cannot serve.
"""

from __future__ import annotations

from sqlalchemy import and_, bindparam, select, text
from sqlalchemy import or_ as sql_or
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.shared.models.api_identity import CredentialScope, canonical_credential_scope

# admin DB — the toolkits the identity is bound to (same statement as the
# broker's runtime resolver; agents and other bound actors share the table).
_AGENT_TOOLKITS = text("SELECT toolkit_id FROM agent_toolkit_bindings WHERE agent_id = :agent_id")

# control DB — the distinct stored credential scopes bound to a set of toolkits.
# Deliberately NOT filtered on ``c.active``: the broker's discovery/derivation
# matcher (``credential_coverage_where()``, used bare by
# ``toolkit_binding_resolver.py``) carries no ``active`` clause, so traffic
# covered by an inactive credential is still intercepted — it fails later, at
# credential injection. Its hosts are therefore still governed; dropping them
# here would tell an integrator's gate to send that traffic direct to the
# upstream, unbrokered. ORDER BY keeps result sets stable for debugging, but
# NULL placement is backend-dependent — deterministic ordering is imposed in
# Python by the caller.
_CREDENTIAL_SCOPES_FOR_TOOLKITS = text(
    "SELECT DISTINCT c.api_vendor, c.api_name, c.api_version "
    "FROM toolkit_credential_bindings tcb "
    "JOIN credentials c ON c.id = tcb.credential_id "
    "WHERE tcb.toolkit_id IN :toolkit_ids "
    "ORDER BY c.api_vendor, c.api_name, c.api_version"
).bindparams(bindparam("toolkit_ids", expanding=True))


class GovernedHostsRepository:
    """Derives the identity's toolkit → credential-scope → host chain.

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
        """Distinct credential scopes bound to the toolkits (**control** DB session).

        Includes inactive credentials — the broker still intercepts their
        traffic (see the note on ``_CREDENTIAL_SCOPES_FOR_TOOLKITS``). Scopes
        are re-canonicalised on read (slugified vendor/name, empty→``None``)
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
        # Canonicalisation can collapse two stored rows into one scope, and the
        # SQL ORDER BY's NULL placement is backend-dependent — dedupe and
        # re-sort in Python for a deterministic result on every backend.
        scopes = {
            canonical_credential_scope(
                vendor=row.api_vendor, name=row.api_name, version=row.api_version
            )
            for row in rows
        }
        return sorted(scopes, key=lambda s: (s.vendor, s.name or "", s.version or ""))

    @staticmethod
    async def hosts_for_scopes(session: AsyncSession, *, scopes: list[CredentialScope]) -> set[str]:
        """Distinct governed host patterns for the scopes (**registry** DB session).

        A ``None`` axis on a scope is the wildcard — the comparison for that axis
        is omitted, so a bare-vendor credential expands to every registered API
        of that vendor (the "wildcard-credential expansion" the issue calls for).

        Hosts come from the URL-match index (``operation_url_indexes.host``) of
        **every indexed revision** of each covered API — the same rows the
        broker's discovery matches against, which applies no revision or state
        predicate (``lookup_by_host_any_revision``) — so archived or superseded
        revisions keep contributing their hosts for exactly as long as they
        keep routing. Variable-bearing hosts (``{var}``) are excluded: the
        index's regex branch never matches them at runtime (see module
        docstring). One query; the API → revision expansion stays inside the
        database, so wildcard credentials over large vendors cannot overflow a
        bind-parameter limit.
        """
        if not scopes:
            return set()

        conditions = []
        for scope in scopes:
            axes = [Api.vendor == scope.vendor]
            if scope.name is not None:
                axes.append(Api.name == scope.name)
            if scope.version is not None:
                axes.append(Api.version == scope.version)
            conditions.append(and_(*axes))

        host_rows = (
            await session.execute(
                select(OperationURLIndex.host)
                .distinct()
                .join(ApiRevision, ApiRevision.id == OperationURLIndex.revision_id)
                .join(Api, Api.id == ApiRevision.api_id)
                .where(
                    sql_or(*conditions),
                    OperationURLIndex.host.is_not(None),
                    OperationURLIndex.host.not_like("%{%"),
                )
            )
        ).all()
        return {row.host for row in host_rows if row.host}
