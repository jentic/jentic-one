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

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import bindparam, text

from jentic_one.shared.auth.grant_policy import (
    GRANT_POLICY_COLUMNS,
    AfterExpiry,
    GrantEndReason,
    GrantPolicy,
    counts_uses,
    grant_deadline,
    grant_end_reason,
)
from jentic_one.shared.broker.protocols import (
    BoundCredential,
    CredentialDerivation,
    EndedBinding,
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

_CANDIDATE_POLICIES = text(
    f"SELECT {GRANT_POLICY_COLUMNS} FROM agent_credential_bindings "
    "WHERE agent_id = :agent_id AND credential_id IN :ids AND suspended = false"
).bindparams(bindparam("ids", expanding=True))


# admin DB — every direct binding the agent holds, with its suspension flag and
# policy (end date, idle limit and its clock). Suspended bindings never
# authorize an execution (suspension is the reversible per-consumer cut-off),
# nor do bindings whose policy has ended; both are read in the same round-trip
# only so a denial can tell them from a missing binding. The policy is
# evaluated in Python against the injectable clock, never in SQL.
_AGENT_CREDENTIALS = text(
    f"SELECT credential_id, rule_set_id, suspended, after_expiry, {GRANT_POLICY_COLUMNS} "
    "FROM agent_credential_bindings WHERE agent_id = :agent_id"
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
# ``c.state = 'connected'`` for the same reason: the connect flow mints its
# credential row upfront in state ``pending`` (token-less until the vendor
# flow completes), and the schema contract (core/schema/credentials.py)
# promises broker resolution skips non-``connected`` rows.
_COVERING_CREDENTIALS = text(
    f"SELECT c.id FROM credentials c "
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
    "WHERE c.id IN :credential_ids AND c.active AND c.state = 'connected' "
    "ORDER BY c.api_vendor, c.api_name, c.api_version"
).bindparams(bindparam("credential_ids", expanding=True))

# control DB — whether an active binding points at a pending credential that
# covers the API. The caller can only recover by completing that existing flow.
_PENDING_COVERING = text(
    f"SELECT c.id FROM credentials c "
    f"WHERE c.id IN :credential_ids AND {credential_coverage_where()} "
    f"AND c.active AND c.state = 'pending' LIMIT 1"
).bindparams(bindparam("credential_ids", expanding=True))

# control DB — every one of the agent's ended-binding credentials that covers
# the API, with its name and whether it is active. Denial path only: the
# caller's credential selector picks among them, and only an active one can be
# held for renewal.
_ENDED_COVERING = text(
    f"SELECT c.id, c.name, c.active FROM credentials c "
    f"WHERE c.id IN :credential_ids AND {credential_coverage_where()} ORDER BY c.id"
).bindparams(bindparam("credential_ids", expanding=True))

# control DB — whether any of the agent's suspended-binding credentials covers
# the API, whatever its state (the connect flow refuses on the same rule).
# Denial path only.
_SUSPENDED_COVERING = text(
    f"SELECT c.id FROM credentials c "
    f"WHERE c.id IN :credential_ids AND {credential_coverage_where()} LIMIT 1"
).bindparams(bindparam("credential_ids", expanding=True))


def _utc_now() -> datetime:
    return datetime.now(UTC)


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

    def __init__(
        self,
        admin_db: DatabaseSession,
        control_db: DatabaseSession,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._admin_db = admin_db
        self._control_db = control_db
        # Wall-clock source for binding-expiry comparisons (injectable for
        # tests): a binding is expired when ``expires_at <= clock()``.
        self._clock = clock

    async def cached_candidates_live(self, agent_id: str, result: CredentialDerivation) -> bool:
        """Check the live deadlines of cached candidates, including edits by another process."""
        ids = [c.credential_id for c in result.credentials]
        async with self._admin_db.read_session() as session:
            rows = (
                await session.execute(_CANDIDATE_POLICIES, {"agent_id": agent_id, "ids": ids})
            ).all()
        now = self._clock()
        return len(rows) == len(ids) and all(
            grant_end_reason(GrantPolicy.from_row(row), now=now) is None for row in rows
        )

    async def derive_credentials(
        self, *, agent_id: str, vendor: str, name: str, version: str
    ) -> CredentialDerivation:
        """Derive the agent's bound credentials for the API, with empty-set context.

        Returns the intersection (active direct bindings ∩ active credentials
        covering the API) plus enough context to pick the right denial
        directive: whether the agent is bound to anything, whether any
        credential serves the API at all, whether a suspended binding covers
        it, whether an active binding covers it through a pending credential,
        and — when bound but unresolved — a nearest-miss credential identity.
        The suspension and pending checks run only on the empty-set path.
        """
        async with self._admin_db.session() as session:
            binding_rows = (await session.execute(_AGENT_CREDENTIALS, {"agent_id": agent_id})).all()
        now = self._clock()
        rule_sets: dict[str, str | None] = {}
        expiries: dict[str, datetime] = {}
        expired_reasons: dict[str, GrantEndReason] = {}
        refuse_ids: set[str] = set()
        suspended_ids: set[str] = set()
        use_counted: set[str] = set()
        for row in binding_rows:
            if row.suspended:
                suspended_ids.add(row.credential_id)
                continue
            policy = GrantPolicy.from_row(row)
            reason = grant_end_reason(policy, now=now)
            if reason is not None:
                expired_reasons[row.credential_id] = reason
                if row.after_expiry == AfterExpiry.REFUSE:
                    refuse_ids.add(row.credential_id)
                continue
            rule_sets[row.credential_id] = row.rule_set_id
            # A use count is checked on every call, so its derivation is never
            # cached (``cacheable``); its time limits still cap ``valid_until``.
            if counts_uses(policy):
                use_counted.add(row.credential_id)
            deadline = grant_deadline(policy)
            if deadline is not None:
                expiries[row.credential_id] = deadline

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
        # (an operator grant), not "fix your credential", so a mismatch
        # would send the wrong signal. Requires the agent to be bound to
        # something (else it is a plain no-binding case).
        mismatch: IdentityMismatch | None = None
        if not candidates and rule_sets and not covering_ids:
            mismatch = await self._nearest_miss(
                set(rule_sets.keys()), vendor=vendor, name=name, version=version
            )

        # Denial path only: a suspended binding covering the API is the
        # operator's cut-off, so the denial must not read as "connect one".
        binding_suspended = False
        if not candidates and suspended_ids:
            binding_suspended = await self._suspended_covers(
                suspended_ids, vendor=vendor, name=name, version=version
            )

        # Likewise an ended binding covering the API: only a renewal helps,
        # and a new connect would route around it. Suspension is reported
        # first by the caller.
        # Listed even beside a live binding, so a call that names an ended one
        # by id is told it ended; it only becomes the denial when nothing is live.
        ended: tuple[EndedBinding, ...] = ()
        if expired_reasons:
            ended = await self._ended_covering(
                expired_reasons, refuse_ids, vendor=vendor, name=name, version=version
            )
        # The one ended binding a hold could be for, when there is exactly one
        # eligible (active); the denial path narrows by the caller's selector.
        denied = bool(ended) and not candidates
        eligible = [e for e in ended if e.active]
        expired_covering = eligible[0].credential_id if denied and len(eligible) == 1 else None

        binding_pending = False
        if not candidates and rule_sets:
            binding_pending = await self._pending_covers(
                set(rule_sets), vendor=vendor, name=name, version=version
            )

        return CredentialDerivation(
            credentials=candidates,
            agent_bound_any=bool(rule_sets),
            api_served=bool(covering_ids),
            identity_mismatch=mismatch,
            binding_suspended=binding_suspended,
            binding_pending=binding_pending,
            binding_expired=denied,
            expired_credential_id=expired_covering,
            expired_reason=ended[0].reason if denied else None,
            expired_holds=expired_covering is not None and expired_covering not in refuse_ids,
            ended_bindings=ended,
            # The earliest moment a candidate binding lapses: a cache must not
            # serve this derivation at or after it.
            valid_until=min(
                (expiries[c.credential_id] for c in candidates if c.credential_id in expiries),
                default=None,
            ),
            # A derivation through a use-counted binding is never cached (see
            # above). Neither is one that reports any ended binding: it can file
            # a renewal hold, and a console renewal that lands on another broker
            # would otherwise leave this one holding a call for a binding that
            # is live again until the TTL runs out.
            cacheable=not any(c.credential_id in use_counted for c in candidates) and not ended,
        )

    async def _pending_covers(
        self, credential_ids: set[str], *, vendor: str, name: str, version: str
    ) -> bool:
        """Whether an active binding points at a pending credential covering the API."""
        async with self._control_db.session() as session:
            row = (
                await session.execute(
                    _PENDING_COVERING,
                    {
                        "credential_ids": sorted(credential_ids),
                        "vendor": vendor,
                        "name": name,
                        "version": version,
                    },
                )
            ).first()
        return row is not None

    async def _suspended_covers(
        self, suspended_ids: set[str], *, vendor: str, name: str, version: str
    ) -> bool:
        """Whether any suspended-binding credential's stored identity covers the API."""
        covering = await self._covering_id(suspended_ids, vendor=vendor, name=name, version=version)
        return covering is not None

    async def _ended_covering(
        self,
        expired_reasons: dict[str, GrantEndReason],
        refuse_ids: set[str],
        *,
        vendor: str,
        name: str,
        version: str,
    ) -> tuple[EndedBinding, ...]:
        """The agent's ended bindings whose credential covers the API, by credential id."""
        async with self._control_db.session() as session:
            rows = (
                await session.execute(
                    _ENDED_COVERING,
                    {
                        "credential_ids": sorted(expired_reasons),
                        "vendor": vendor,
                        "name": name,
                        "version": version,
                    },
                )
            ).all()
        return tuple(
            EndedBinding(
                credential_id=str(row[0]),
                name=row[1],
                reason=expired_reasons[str(row[0])].value,
                holds=str(row[0]) not in refuse_ids,
                active=bool(row[2]),
            )
            for row in rows
        )

    async def _covering_id(
        self, credential_ids: set[str], *, vendor: str, name: str, version: str
    ) -> str | None:
        """One of ``credential_ids`` whose stored identity covers the API, whatever its state."""
        async with self._control_db.session() as session:
            row = (
                await session.execute(
                    _SUSPENDED_COVERING,
                    {
                        "credential_ids": sorted(credential_ids),
                        "vendor": vendor,
                        "name": name,
                        "version": version,
                    },
                )
            ).first()
        return str(row[0]) if row is not None else None

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
