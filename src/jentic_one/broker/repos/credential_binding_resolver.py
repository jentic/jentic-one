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

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.shared.broker.protocols import (
    BoundCredential,
    BoundCredentialHeader,
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

# admin DB — every direct binding the agent holds, with its suspension flag.
# Suspended bindings never authorize an execution (suspension is the
# reversible per-consumer cut-off); they are read in the same round-trip only
# so a denial can tell a suspended binding from a missing one.
_AGENT_CREDENTIALS = text(
    "SELECT credential_id, rule_set_id, suspended FROM agent_credential_bindings "
    "WHERE agent_id = :agent_id"
)

# control DB — active credentials whose stored identity covers the API. The
# coverage rule (NULL credential axis = "unscoped → covers any"; otherwise
# equality against the always-concrete operation axis) is the shared seam in
# shared/models/api_identity.py, so this matcher and the injection-time
# resolver
# (broker/services/credentials/resolver) cannot drift apart.
#
# Selects the non-secret header columns alongside ``c.id`` so downstream
# selection (``CredentialService.select`` / ``CredentialResolver.resolve``)
# can disambiguate candidates without querying ``credentials`` a second time.
#
# ``c.active`` is filtered here (unlike the deleted pre-6b toolkit-path
# matcher, which deferred
# the active check to injection): a disabled credential must not surface as a
# selection candidate — it could only turn a clean 403 into a confusing 424
# later, or force a spurious ambiguity 409 against a live sibling.
# ``c.state = 'connected'`` for the same reason: the connect flow mints its
# credential row upfront in state ``pending`` (token-less until the vendor
# flow completes), and the schema contract (core/schema/credentials.py)
# promises broker resolution skips non-``connected`` rows.
_COVERING_CREDENTIALS = text(
    "SELECT c.id, c.type, c.name, c.api_vendor, c.api_name, c.api_version, "
    "c.provider, c.provider_account_ref, c.server_variables, "
    "c.created_at, c.created_by "
    f"FROM credentials c "
    f"WHERE {credential_coverage_where()} AND c.active AND c.state = 'connected'"
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


def _coerce_server_variables(value: object) -> dict[str, str] | None:
    """Coerce a raw-SQL ``server_variables`` column value into a string dict."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if isinstance(value, Mapping):
        return {str(k): str(v) for k, v in value.items()}
    return None


def _coerce_datetime(value: object) -> datetime | None:
    """Coerce a raw-SQL timestamp column value into a timezone-aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    return None


def _to_header(row: Any) -> BoundCredentialHeader:
    """Build a :class:`BoundCredentialHeader` from a ``_COVERING_CREDENTIALS`` row."""
    return BoundCredentialHeader(
        id=row.id,
        type=row.type,
        name=row.name,
        api_vendor=row.api_vendor,
        api_name=row.api_name,
        api_version=row.api_version,
        provider=row.provider,
        provider_account_ref=row.provider_account_ref,
        active=True,
        server_variables=_coerce_server_variables(row.server_variables),
        created_at=_coerce_datetime(row.created_at),
        created_by=row.created_by,
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
        credential serves the API at all, whether a suspended binding covers
        it, and — when bound but unresolved — a nearest-miss credential
        identity. The suspension check costs a query only on the empty-set
        path, and only when the agent holds a suspended binding.
        """
        async with self._admin_db.read_session() as session:
            binding_rows = (await session.execute(_AGENT_CREDENTIALS, {"agent_id": agent_id})).all()
        rule_sets: dict[str, str | None] = {row[0]: row[1] for row in binding_rows if not row[2]}
        suspended_ids = {row[0] for row in binding_rows if row[2]}

        async with self._control_db.read_session() as session:
            covering_rows = (
                await session.execute(
                    _COVERING_CREDENTIALS,
                    {"vendor": vendor, "name": name, "version": version},
                )
            ).all()
            covering_by_id = {row.id: row for row in covering_rows}
            covering_ids = set(covering_by_id)

            # Sorted for a deterministic, cache-stable candidate order.
            candidates = tuple(
                BoundCredential(
                    credential_id=cid,
                    rule_set_id=rule_sets[cid],
                    header=_to_header(covering_by_id[cid]),
                )
                for cid in sorted(rule_sets.keys() & covering_ids)
            )

            # Nearest-miss diagnostic only when nothing covers the API at all: if a
            # credential covers it (``covering_ids``), the recovery is "bind to it"
            # (an operator grant), not "fix your credential", so a mismatch
            # would send the wrong signal. Requires the agent to be bound to
            # something (else it is a plain no-binding case).
            mismatch: IdentityMismatch | None = None
            if not candidates and rule_sets and not covering_ids:
                mismatch = await self._nearest_miss(
                    session, set(rule_sets.keys()), vendor=vendor, name=name, version=version
                )

            # Denial path only: a suspended binding covering the API is the
            # operator's cut-off, so the denial must not read as "connect one".
            binding_suspended = False
            if not candidates and suspended_ids:
                binding_suspended = await self._suspended_covers(
                    session, suspended_ids, vendor=vendor, name=name, version=version
                )

        return CredentialDerivation(
            credentials=candidates,
            agent_bound_any=bool(rule_sets),
            api_served=bool(covering_ids),
            identity_mismatch=mismatch,
            binding_suspended=binding_suspended,
        )

    async def _suspended_covers(
        self,
        session: AsyncSession,
        suspended_ids: set[str],
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

    async def _nearest_miss(
        self,
        session: AsyncSession,
        bound_credential_ids: set[str],
        *,
        vendor: str,
        name: str,
        version: str,
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
