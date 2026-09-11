"""Cross-DB direct-binding credential derivation (theme-5 Phase 2).

Given an agent and a resolved API identity, returns the credentials the agent
is **directly and actively bound to** whose stored identity covers that API.
The lookup is
inherently cross-schema (admin ``agent_credential_bindings`` + control
``credentials``) — the broker may import neither ``admin`` nor ``control`` ORM,
so it runs as raw SQL behind ``CredentialDeriverProtocol``.

The two databases are separate sessions, so the intersection is computed in
Python rather than via a cross-schema JOIN.
"""

from __future__ import annotations

from sqlalchemy import bindparam, text

from jentic_one.shared.broker.protocols import (
    BoundCredential,
    CredentialDerivation,
    IdentityMismatch,
)
from jentic_one.shared.db import DatabaseSession
from jentic_one.shared.models.api_identity import (
    CredentialScope,
    canonical_credential_scope,
    credential_coverage_where,
    credential_covers,
    slugify_api_field,
)

# admin DB — the credentials the agent is directly and actively bound to.
# Suspended bindings are excluded: suspension is the reversible per-consumer
# cut-off, so a suspended binding must not authorize an execution.
_AGENT_CREDENTIALS = text(
    "SELECT credential_id, rule_set_id FROM agent_credential_bindings "
    "WHERE agent_id = :agent_id AND suspended = false"
)

# control DB — active credentials whose stored identity covers the API. The
# coverage rule (NULL credential axis = "unscoped → covers any"; otherwise
# equality against the always-concrete operation axis) is the shared seam in
# shared/models/api_identity.py, so this matcher and the injection-time
# resolver
# (broker/services/credentials/resolver) cannot drift apart.
#
# ``c.active`` is filtered here (unlike the deleted pre-6b toolkit-path
# matcher, which deferred
# the active check to injection): a disabled credential must not surface as a
# selection candidate — it could only turn a clean 403 into a confusing 424
# later, or force a spurious ambiguity 409 against a live sibling.
_COVERING_CREDENTIALS = text(
    f"SELECT c.id FROM credentials c WHERE {credential_coverage_where()} AND c.active"
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
    """Derives an agent's directly-bound credentials for an API identity.

    Cross-DB: active bindings (admin) ∩ active covering credentials (control).
    Implements ``CredentialDeriverProtocol``.
    """

    def __init__(self, admin_db: DatabaseSession, control_db: DatabaseSession) -> None:
        self._admin_db = admin_db
        self._control_db = control_db

    async def derive_credentials(
        self, *, agent_id: str, vendor: str, name: str, version: str
    ) -> CredentialDerivation:
        """Derive the agent's bound credentials for the API, with empty-set context.

        Returns the intersection (active direct bindings ∩ active credentials
        covering the API) plus enough context to pick the right denial
        directive: whether the agent is bound to anything, whether any
        credential serves the API at all, and — when bound but unresolved — a
        nearest-miss credential identity.
        """
        async with self._admin_db.session() as session:
            binding_rows = (await session.execute(_AGENT_CREDENTIALS, {"agent_id": agent_id})).all()
        rule_sets: dict[str, str | None] = {row[0]: row[1] for row in binding_rows}

        async with self._control_db.session() as session:
            covering_rows = (
                await session.execute(
                    _COVERING_CREDENTIALS,
                    {"vendor": vendor, "name": name, "version": version},
                )
            ).all()
        covering_ids = {row[0] for row in covering_rows}

        # Sorted for a deterministic, cache-stable candidate order.
        candidates = tuple(
            BoundCredential(credential_id=cid, rule_set_id=rule_sets[cid])
            for cid in sorted(rule_sets.keys() & covering_ids)
        )

        # Nearest-miss diagnostic only when nothing covers the API at all: if a
        # credential covers it (``covering_ids``), the recovery is "bind to it"
        # (file an access request), not "fix your credential", so a mismatch
        # would send the wrong signal. Requires the agent to be bound to
        # something (else it is a plain no-binding case).
        mismatch: IdentityMismatch | None = None
        if not candidates and rule_sets and not covering_ids:
            mismatch = await self._nearest_miss(
                set(rule_sets.keys()), vendor=vendor, name=name, version=version
            )

        return CredentialDerivation(
            credentials=candidates,
            agent_bound_any=bool(rule_sets),
            api_served=bool(covering_ids),
            identity_mismatch=mismatch,
        )

    async def _nearest_miss(
        self, bound_credential_ids: set[str], *, vendor: str, name: str, version: str
    ) -> IdentityMismatch | None:
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
        async with self._control_db.session() as session:
            rows = (
                await session.execute(
                    _CREDENTIAL_IDENTITIES,
                    {"credential_ids": sorted(bound_credential_ids)},
                )
            ).all()
        if not rows:
            return None

        expected_vendor_slug = slugify_api_field(vendor)
        best: IdentityMismatch | None = None
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
                best = IdentityMismatch(
                    expected_vendor=vendor,
                    expected_name=name,
                    expected_version=version,
                    found_vendor=row.api_vendor,
                    found_name=row.api_name,
                    found_version=row.api_version,
                    would_match_if_normalized=would_match,
                )
        return best
