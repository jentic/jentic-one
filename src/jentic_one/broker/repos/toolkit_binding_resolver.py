"""Cross-DB toolkit-derivation resolver (admin bindings ∩ control credential bindings).

Given an agent and a resolved API identity, returns the agent's toolkit IDs that
contain that API. This is inherently cross-schema (admin agent→toolkit bindings +
control toolkit→credential bindings), and the broker may import neither ``admin``
nor ``control`` ORM — so it runs as raw SQL behind ``ToolkitDeriverProtocol``.

The two databases are separate sessions, so the intersection is computed in
Python rather than via a cross-schema JOIN.
"""

from __future__ import annotations

from sqlalchemy import bindparam, text

from jentic_one.shared.broker.protocols import IdentityMismatch, ToolkitDerivation
from jentic_one.shared.db import DatabaseSession
from jentic_one.shared.models.api_identity import (
    canonical_credential_scope,
    credential_coverage_where,
    credential_covers,
)

# admin DB — the toolkits the agent is bound to.
_AGENT_TOOLKITS = text("SELECT toolkit_id FROM agent_toolkit_bindings WHERE agent_id = :agent_id")

# control DB — toolkits whose bound credential covers the API identity. The
# coverage rule (NULL credential axis = "unscoped → covers any"; otherwise
# equality against the always-concrete operation axis) is the shared seam in
# shared/models/api_identity.py, so this matcher, the bind-time matcher
# (control/repos/effects_repo) and the injection-time resolver
# (broker/services/credentials/resolver) cannot drift apart.
#
# NB: this run-time resolver intentionally treats a NULL-wildcard credential
# binding as a valid match and applies *no* exact-name preference or specificity
# narrowing — at execution time we want every toolkit the agent can legitimately
# use, including wildcard-credential ones (>1 match surfaces as ambiguous_toolkit,
# which the agent resolves with the Jentic-Toolkit-Id header). This differs from
# the *bind-time* resolver, which prefers an exact api_name match so the approver
# binds the most specific toolkit. The two serve different purposes (credential
# coverage vs ownership-scoped binding selection), so the asymmetry is deliberate.
_TOOLKITS_FOR_API = text(
    "SELECT DISTINCT tcb.toolkit_id "
    "FROM toolkit_credential_bindings tcb "
    "JOIN credentials c ON c.id = tcb.credential_id "
    f"WHERE {credential_coverage_where()}"
)

# control DB — the stored credential identities bound to a given set of toolkits.
# Used only on the denial path to compute a nearest-miss diagnostic (#747/#748).
# Deliberately NOT vendor-scoped: a non-canonical stored vendor is exactly the
# mismatch we want to surface, so filtering by vendor would hide it.
_CREDENTIAL_IDENTITIES_FOR_TOOLKITS = text(
    "SELECT DISTINCT c.api_vendor, c.api_name, c.api_version "
    "FROM toolkit_credential_bindings tcb "
    "JOIN credentials c ON c.id = tcb.credential_id "
    "WHERE tcb.toolkit_id IN :toolkit_ids"
).bindparams(bindparam("toolkit_ids", expanding=True))


class ToolkitBindingResolver:
    """Derives an agent's toolkits for an API identity across the admin + control DBs."""

    def __init__(self, admin_db: DatabaseSession, control_db: DatabaseSession) -> None:
        self._admin_db = admin_db
        self._control_db = control_db

    async def derive_toolkits(
        self, *, agent_id: str, vendor: str, name: str, version: str
    ) -> ToolkitDerivation:
        """Derive the agent's toolkits for the API, with the reason for an empty set.

        Returns the intersection (agent bindings ∩ toolkits whose credential
        covers the API) plus enough context to pick the right denial directive:
        whether the agent is bound to anything, which toolkits serve the API at
        all, and — when bound but unresolved — a nearest-miss credential identity.
        """
        async with self._admin_db.session() as session:
            agent_rows = (await session.execute(_AGENT_TOOLKITS, {"agent_id": agent_id})).all()
        agent_toolkits = {row[0] for row in agent_rows}

        async with self._control_db.session() as session:
            api_rows = (
                await session.execute(
                    _TOOLKITS_FOR_API,
                    {"vendor": vendor, "name": name, "version": version},
                )
            ).all()
        api_toolkits = {row[0] for row in api_rows}

        intersection = tuple(sorted(agent_toolkits & api_toolkits))
        served = tuple(sorted(api_toolkits))

        mismatch: IdentityMismatch | None = None
        if not intersection and agent_toolkits and not api_toolkits:
            mismatch = await self._nearest_miss(
                agent_toolkits, vendor=vendor, name=name, version=version
            )

        return ToolkitDerivation(
            toolkits=intersection,
            agent_bound_any=bool(agent_toolkits),
            api_served_toolkits=served,
            identity_mismatch=mismatch,
        )

    async def _nearest_miss(
        self, agent_toolkits: set[str], *, vendor: str, name: str, version: str
    ) -> IdentityMismatch | None:
        """Find the closest bound-credential identity that fails to cover the API.

        The agent is bound but nothing serves the API. Inspect the identities of
        credentials bound to the agent's own toolkits and report the nearest one,
        flagging whether it would cover the operation once canonicalized (the
        #746 legacy-row signal). Returns ``None`` if the agent's toolkits have no
        bound credentials at all (then it is a genuine provisioning gap, not a
        mismatch).
        """
        async with self._control_db.session() as session:
            rows = (
                await session.execute(
                    _CREDENTIAL_IDENTITIES_FOR_TOOLKITS,
                    {"toolkit_ids": list(agent_toolkits)},
                )
            ).all()
        if not rows:
            return None

        best: IdentityMismatch | None = None
        for row in rows:
            scope = canonical_credential_scope(
                vendor=row.api_vendor, name=row.api_name, version=row.api_version
            )
            would_match = credential_covers(scope, vendor=vendor, name=name, version=version)
            candidate = IdentityMismatch(
                expected_vendor=vendor,
                expected_name=name,
                expected_version=version,
                found_vendor=row.api_vendor,
                found_name=row.api_name,
                found_version=row.api_version,
                would_match_if_normalized=would_match,
            )
            # A would-match-after-normalization row is the most actionable signal
            # (#746), so prefer it; otherwise keep the first candidate seen.
            if would_match:
                return candidate
            if best is None:
                best = candidate
        return best
