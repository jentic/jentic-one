"""Direct-binding derivation and unserved-API diagnosis (theme-5 Phase 2).

``derive_bindings`` reads an agent's direct bindings from the admin DB. The
broker intersects them with the covering credentials it reads live from the
control DB (``CredentialRepository.list_covering``, via ``CredentialService``),
so credential state is never served from this module's cache.

``diagnose_unserved`` runs only when that intersection is empty: it reads the
control DB to explain the denial (does anything serve the API, is a bound
credential a near miss, is a covering binding suspended). The broker may import
neither ``admin`` nor ``control`` ORM, so both run as raw SQL behind
``CredentialDeriverProtocol``.
"""

from __future__ import annotations

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.shared.broker.protocols import (
    AgentBindings,
    BoundCredential,
    CredentialScopeMiss,
    UnservedDiagnosis,
)
from jentic_one.shared.db import DatabaseSession
from jentic_one.shared.models.api_identity import (
    CredentialScope,
    canonical_credential_scope,
    credential_coverage_where,
    credential_covers,
    slugify_api_field,
)

# admin DB — every direct binding the agent holds, with its suspension flag.
# Suspended bindings never authorize an execution (suspension is the
# reversible per-consumer cut-off); they are read in the same round-trip only
# so a denial can tell a suspended binding from a missing one.
_AGENT_CREDENTIALS = text(
    "SELECT credential_id, rule_set_id, suspended FROM agent_credential_bindings "
    "WHERE agent_id = :agent_id"
)

# control DB — whether any active, connected credential (any owner) covers the
# API. Denial path only; drives the "grant a binding" vs. "provision a
# credential first" directive. The coverage rule (NULL credential axis =
# "unscoped → covers any"; otherwise equality against the always-concrete
# operation axis) is the shared seam in shared/models/api_identity.py, so this
# and the live candidate read (``CredentialRepository.list_covering``) cannot
# drift apart. ``c.active`` / ``c.state = 'connected'`` match that read: a
# disabled or still-``pending`` credential serves nothing.
_API_SERVED = text(
    f"SELECT 1 FROM credentials c "
    f"WHERE {credential_coverage_where()} AND c.active AND c.state = 'connected' LIMIT 1"
)

# control DB — the stored identities of a set of credentials. Used only on the
# denial path to compute a nearest-miss diagnostic (#747/#748 twin). Only
# active rows are considered so the diagnostic never names a disabled
# credential, and rows are ordered so the "closest miss" choice is
# deterministic across backends.
_CREDENTIAL_IDENTITIES = text(
    "SELECT DISTINCT c.api_vendor, c.api_name, c.api_version "
    "FROM credentials c "
    "WHERE c.id IN :credential_ids AND c.active "
    "ORDER BY c.api_vendor, c.api_name, c.api_version"
).bindparams(bindparam("credential_ids", expanding=True))

# control DB — whether any of the agent's suspended-binding credentials covers
# the API, whatever its state (the connect flow refuses on the same rule).
# Denial path only.
_SUSPENDED_COVERING = text(
    f"SELECT c.id FROM credentials c "
    f"WHERE c.id IN :credential_ids AND {credential_coverage_where()} LIMIT 1"
).bindparams(bindparam("credential_ids", expanding=True))


def _axes_matched(scope: CredentialScope, *, vendor: str, name: str, version: str) -> int:
    """How many of the operation's axes this scope matches (near-miss ranking).

    An unscoped (NULL) axis does not
    *match* a concrete value for ranking purposes — a wildcard would have
    *covered* the operation and never reached the near-miss path. Ranking only;
    never an authorization signal.
    """
    return (
        int(scope.vendor == slugify_api_field(vendor))
        + int(scope.name is not None and scope.name == slugify_api_field(name))
        + int(scope.version is not None and scope.version == version.strip())
    )


class CredentialBindingResolver:
    """Reads an agent's direct bindings and diagnoses an unserved API.

    Implements ``CredentialDeriverProtocol``.
    """

    def __init__(self, admin_db: DatabaseSession, control_db: DatabaseSession) -> None:
        self._admin_db = admin_db
        self._control_db = control_db

    async def derive_bindings(self, *, agent_id: str) -> AgentBindings:
        """The agent's active bindings (sorted by credential id) and suspended ids."""
        async with self._admin_db.read_session() as session:
            rows = (await session.execute(_AGENT_CREDENTIALS, {"agent_id": agent_id})).all()
        return AgentBindings(
            agent_id=agent_id,
            active=tuple(
                BoundCredential(credential_id=row[0], rule_set_id=row[1])
                for row in sorted(rows, key=lambda r: r[0])
                if not row[2]
            ),
            suspended_ids=frozenset(row[0] for row in rows if row[2]),
        )

    async def diagnose_unserved(
        self, *, bindings: AgentBindings, vendor: str, name: str, version: str
    ) -> UnservedDiagnosis:
        """Explain why none of ``bindings`` serves the API, in one control read session.

        Called only when no actively bound credential covers the API. Costs
        one query for ``api_served``, plus one for the nearest miss when the
        agent is bound but nothing serves the API, plus one for the suspension
        check when the agent holds a suspended binding.
        """
        params = {"vendor": vendor, "name": name, "version": version}
        async with self._control_db.read_session() as session:
            api_served = (await session.execute(_API_SERVED, params)).first() is not None

            # Nearest-miss diagnostic only when nothing covers the API at all: if a
            # credential covers it, the recovery is "bind to it" (an operator
            # grant), not "fix your credential", so a mismatch would send the
            # wrong signal. Requires the agent to be bound to something (else it
            # is a plain no-binding case).
            miss: CredentialScopeMiss | None = None
            if bindings.active and not api_served:
                miss = await self._nearest_miss(
                    session, set(bindings.active_ids), vendor=vendor, name=name, version=version
                )

            # A suspended binding covering the API is the operator's cut-off, so
            # the denial must not read as "connect one".
            binding_suspended = False
            if bindings.suspended_ids:
                binding_suspended = await self._suspended_covers(
                    session, bindings.suspended_ids, vendor=vendor, name=name, version=version
                )

        return UnservedDiagnosis(
            agent_bound_any=bool(bindings.active),
            api_served=api_served,
            scope_miss=miss,
            binding_suspended=binding_suspended,
        )

    @staticmethod
    async def _suspended_covers(
        session: AsyncSession,
        suspended_ids: frozenset[str],
        *,
        vendor: str,
        name: str,
        version: str,
    ) -> bool:
        """Whether any suspended-binding credential's stored identity covers the API."""
        row = (
            await session.execute(
                _SUSPENDED_COVERING,
                {
                    "credential_ids": sorted(suspended_ids),
                    "vendor": vendor,
                    "name": name,
                    "version": version,
                },
            )
        ).first()
        return row is not None

    @staticmethod
    async def _nearest_miss(
        session: AsyncSession,
        bound_credential_ids: set[str],
        *,
        vendor: str,
        name: str,
        version: str,
    ) -> CredentialScopeMiss | None:
        """Find the closest bound-credential identity that fails to cover the API.

        Same vendor-affinity gate and ranking as the deleted toolkit-path twin
        used (theme-5 Phase 6b): only a same-vendor or
        ``would_match_if_normalized`` (#746 legacy slug) row is a genuine
        identity mismatch for *this* API — a credential for an unrelated vendor
        is not a mismatch, and returning it would wrongly tell the operator to
        retarget a working credential. Deterministic: rows arrive ordered, and
        among gated candidates ``would_match_if_normalized`` wins, then the most
        axes matching the operation.
        """
        rows = (
            await session.execute(
                _CREDENTIAL_IDENTITIES,
                {"credential_ids": sorted(bound_credential_ids)},
            )
        ).all()
        if not rows:
            return None

        expected_vendor_slug = slugify_api_field(vendor)
        best: CredentialScopeMiss | None = None
        best_rank: tuple[int, int] = (-1, -1)
        for row in rows:
            scope = canonical_credential_scope(
                vendor=row.api_vendor, name=row.api_name, version=row.api_version
            )
            would_match = credential_covers(scope, vendor=vendor, name=name, version=version)
            if scope.vendor != expected_vendor_slug and not would_match:
                continue
            rank = (
                int(would_match),
                _axes_matched(scope, vendor=vendor, name=name, version=version),
            )
            if rank > best_rank:
                best_rank = rank
                best = CredentialScopeMiss(
                    expected_vendor=vendor,
                    expected_name=name,
                    expected_version=version,
                    found_vendor=row.api_vendor,
                    found_name=row.api_name,
                    found_version=row.api_version,
                    would_match_if_normalized=would_match,
                )
        return best
