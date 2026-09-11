"""Theme-5 Phase 6a — the toolkit-flattening job (no drops).

Derives direct ``agent_credential_bindings`` rows (plus shared
``permission_rule_sets``) from the legacy toolkit graph: every
``(agent, credential)`` pair reachable via ``agent_toolkit_bindings ⋈
toolkit_credential_bindings`` gains a direct binding carrying the pair's
effective rules from ``toolkit_permission_rules``. Output is shaped exactly
like a hand-created binding (same tables, same rule-set indirection), so the
broker's direct path serves flattened and hand-made pairs identically.

Semantics for the two ways the legacy model cannot be flattened soundly
(theme plan, hard problem 2):

- **Same-pair rule conflicts** — a pair reachable via two toolkits whose
  per-pair rule lists diverge has no correct automatic merge (ordered
  first-match-wins lists). The job prefers the SAFER outcome: the binding is
  created **default-deny** (no rule set, no inline rules) and a
  ``rule_conflict`` report line embeds every contributing list verbatim for
  the operator to resolve.
- **Pooled-vs-per-pair drift** — the legacy broker pooled rules per
  ``(toolkit, api_vendor)``, so a vendor-wide rule authored on one binding
  also governed sibling same-vendor credentials. The direct model is strictly
  per-pair. The job writes the per-pair list (the target model) and emits a
  ``pooled_rule_drift`` line wherever the pooled list differs, embedding both
  lists verbatim.

Ordering and idempotency: rules are written **control-first**, bindings
**admin-last**, so a crash mid-run leaves orphan rule sets — never a live
rule-less binding — and a re-run skips pairs that already exist (the natural
``UNIQUE (agent_id, credential_id)`` on the binding). Quiescing bind/unbind
is not practical in OSS, so concurrency is handled by double-run-and-diff:
run the job, run it again, and assert the second run reports zero creations
(``--diff-only`` previews without writing).

Bindings reachable only through **inactive** toolkits are live access on the
legacy path (``toolkits.active`` never gated bound agents) — they are
migrated like any other pair AND reported loudly, never skipped.

Operator-invoked only (``jentic_one flatten-toolkits``): unlike the Phase-4
key retirement there is no startup one-shot, because Phase 6b's drops are
gated on an explicit operator acknowledgement (``--verify --acknowledge``
writes the ``toolkit_flattening_acks`` sentinel row 6b's migrations check).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

import structlog

from jentic_one import __version__
from jentic_one.control.repos.permission_rule_set_repo import PermissionRuleSetRepository
from jentic_one.control.repos.toolkit_flattening_repo import (
    SYSTEM_ACTOR,
    FlatteningAdminRepository,
    FlatteningControlRepository,
    ToolkitPermissionRuleRow,
)
from jentic_one.shared.audit import record_audit
from jentic_one.shared.context import Context
from jentic_one.shared.models.audit import AuditAction, AuditTargetType

logger = structlog.get_logger(__name__)

#: ``audit_entries.actor_type`` value for job-derived writes. Not a member of
#: ``ActorType`` (jobs are not authenticated actors); the audit read surface
#: treats the column as an opaque string.
_AUDIT_ACTOR_TYPE = "system:job"

#: The only scope a converted ``jntc_live_`` holder should carry (Phase 4).
_EXECUTE_SCOPE = "capabilities:execute"


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _rule_row(rule: ToolkitPermissionRuleRow | Any) -> dict[str, Any]:
    """One legacy rule row, verbatim — report lines must survive the drop."""
    return {
        "id": rule.id,
        "toolkit_id": rule.toolkit_id,
        "credential_id": rule.credential_id,
        "effect": rule.effect,
        "methods": rule.methods,
        "path": rule.path,
        "match_mode": rule.match_mode,
        "operations": rule.operations,
        "is_system": rule.is_system,
        "comment": rule.comment,
        "sequence": rule.sequence,
        "created_at": _iso(rule.created_at),
        "created_by": rule.created_by,
    }


def _rule_semantics(rule: Any) -> tuple[Any, ...]:
    """The fields rule evaluation reads — the equality key for conflict checks."""
    return (
        rule.effect,
        tuple(rule.methods) if rule.methods is not None else None,
        rule.path,
        rule.match_mode,
        tuple(rule.operations) if rule.operations is not None else None,
    )


@dataclass(frozen=True)
class Finding:
    """One JSONL report line: a category plus its category-specific payload."""

    category: str
    detail: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"category": self.category, **self.detail}


@dataclass
class FlatteningRunResult:
    """Outcome of one flattening run (or ``--diff-only`` preview)."""

    diff_only: bool
    pairs_total: int = 0
    created: int = 0
    already_present: int = 0
    backfilled_execution_names: int = 0
    findings: list[Finding] = field(default_factory=list)


@dataclass
class VerificationResult:
    """Outcome of ``--verify`` (R-02): counts, coverage, and report lines."""

    passed: bool
    legacy_pair_count: int
    direct_binding_count: int
    missing_pair_count: int
    unbackfilled_execution_name_count: int = 0
    findings: list[Finding] = field(default_factory=list)
    acknowledged: bool = False


@dataclass
class _Path:
    """One toolkit route from an agent to a credential."""

    toolkit_id: str
    toolkit_name: str
    toolkit_active: bool
    atb_id: str
    tcb_id: str
    bound_at: dt.datetime | None  # max(atb.bound_at, tcb.bound_at) for this path
    rules: list[Any]  # per-pair ToolkitPermissionRule rows, evaluation order
    pooled_rules: list[Any]  # (toolkit, vendor) pooled rows, broker order


@dataclass
class _Snapshot:
    """Everything the job reads, loaded once per run from both databases."""

    toolkits: dict[str, Any]
    credentials: dict[str, Any]
    pair_rules: dict[tuple[str, str], list[Any]]
    pooled_rules: dict[tuple[str, str], list[Any]]
    tcb_rows: list[Any]
    atb_rows: list[tuple[str, str, str, dt.datetime | None]]
    toolkit_keys: list[Any]
    actor_ids: set[str]
    existing_pairs: dict[tuple[str, str], str | None]
    scopes_by_actor: dict[str, list[str]]


@dataclass
class _DerivedPair:
    """One (agent, credential) pair with every toolkit path that reaches it."""

    agent_id: str
    credential_id: str
    paths: list[_Path] = field(default_factory=list)

    @property
    def conflicting(self) -> bool:
        """True when the contributing per-pair rule lists diverge."""
        distinct = {tuple(_rule_semantics(r) for r in p.rules) for p in self.paths}
        return len(distinct) > 1

    @property
    def target_rules(self) -> list[Any]:
        """The rule list the direct binding gets: the identical contributors'
        list, or empty (default-deny, the safer outcome) on conflict."""
        if self.conflicting:
            return []
        return self.paths[0].rules

    @property
    def source_path(self) -> _Path:
        """Deterministic provenance path: the lowest toolkit id."""
        return min(self.paths, key=lambda p: p.toolkit_id)

    @property
    def bound_at(self) -> dt.datetime:
        """max(source rows' timestamps) across every contributing path."""
        stamps = [p.bound_at for p in self.paths if p.bound_at is not None]
        return max(stamps) if stamps else dt.datetime.now(dt.UTC)


def _rule_set_name(toolkit_id: str, credential_id: str) -> str:
    """Deterministic per-source-pair name — the job's control-side idempotency key."""
    return f"theme5-flattening:{toolkit_id}:{credential_id}"


class ToolkitFlatteningService:
    """Orchestrates the flattening run across the control and admin databases."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def _load_snapshot(self) -> _Snapshot:
        """One coherent read of both databases (torn reads against live binds
        are handled by the double-run-and-diff runbook, not by locking)."""
        async with self._ctx.control_db.session() as control_session:
            toolkits = await FlatteningControlRepository.list_toolkits(control_session)
            credentials = await FlatteningControlRepository.list_credentials(control_session)
            tcb_rows = await FlatteningControlRepository.list_credential_bindings(control_session)
            rules = await FlatteningControlRepository.list_permission_rules(control_session)
            toolkit_keys = await FlatteningControlRepository.list_toolkit_keys(control_session)

        async with self._ctx.admin_db.session() as admin_session:
            atb_rows = await FlatteningAdminRepository.list_agent_toolkit_bindings(admin_session)
            actor_ids = await FlatteningAdminRepository.list_actor_ids(admin_session)
            existing_pairs = await FlatteningAdminRepository.list_direct_binding_pairs(
                admin_session
            )
            scopes_by_actor = await FlatteningAdminRepository.list_scopes_by_actor(admin_session)

        toolkit_map = {t.id: t for t in toolkits}
        credential_map = {c.id: c for c in credentials}

        pair_rules: dict[tuple[str, str], list[Any]] = {}
        for rule in rules:  # repo orders (toolkit, credential, is_system, sequence)
            pair_rules.setdefault((rule.toolkit_id, rule.credential_id), []).append(rule)

        # The legacy broker pool: rules of every *bound* same-vendor pair of
        # the toolkit, ordered by sequence alone (list_rules_for_vendor's
        # ORDER BY — ties across sibling credentials interleave, which is
        # faithful to what the broker evaluated).
        bound_pairs = {(b.toolkit_id, b.credential_id) for b in tcb_rows}
        pooled_rules: dict[tuple[str, str], list[Any]] = {}
        for (toolkit_id, credential_id), rows in pair_rules.items():
            if (toolkit_id, credential_id) not in bound_pairs:
                continue
            credential = credential_map.get(credential_id)
            if credential is None:
                continue
            pooled_rules.setdefault((toolkit_id, credential.api_vendor), []).extend(rows)
        for pool in pooled_rules.values():
            pool.sort(key=lambda r: (r.sequence, r.id))

        return _Snapshot(
            toolkits=toolkit_map,
            credentials=credential_map,
            pair_rules=pair_rules,
            pooled_rules=pooled_rules,
            tcb_rows=tcb_rows,
            atb_rows=atb_rows,
            toolkit_keys=toolkit_keys,
            actor_ids=actor_ids,
            existing_pairs=existing_pairs,
            scopes_by_actor=scopes_by_actor,
        )

    @staticmethod
    def _derive_pairs(
        snapshot: _Snapshot, findings: list[Finding]
    ) -> dict[tuple[str, str], _DerivedPair]:
        """Join atb ⋈ tcb in the app layer, reporting dangling rows as found."""
        tcb_by_toolkit: dict[str, list[Any]] = {}
        for tcb in snapshot.tcb_rows:
            if tcb.credential_id not in snapshot.credentials:
                findings.append(
                    Finding(
                        "dangling_reference",
                        {
                            "table": "toolkit_credential_bindings",
                            "row_id": tcb.id,
                            "toolkit_id": tcb.toolkit_id,
                            "missing": "credential",
                            "missing_id": tcb.credential_id,
                        },
                    )
                )
                continue
            if tcb.toolkit_id not in snapshot.toolkits:
                findings.append(
                    Finding(
                        "dangling_reference",
                        {
                            "table": "toolkit_credential_bindings",
                            "row_id": tcb.id,
                            "credential_id": tcb.credential_id,
                            "missing": "toolkit",
                            "missing_id": tcb.toolkit_id,
                        },
                    )
                )
                continue
            tcb_by_toolkit.setdefault(tcb.toolkit_id, []).append(tcb)

        pairs: dict[tuple[str, str], _DerivedPair] = {}
        for atb_id, agent_id, toolkit_id, atb_bound_at in snapshot.atb_rows:
            toolkit = snapshot.toolkits.get(toolkit_id)
            if toolkit is None:
                findings.append(
                    Finding(
                        "dangling_reference",
                        {
                            "table": "agent_toolkit_bindings",
                            "row_id": atb_id,
                            "agent_id": agent_id,
                            "missing": "toolkit",
                            "missing_id": toolkit_id,
                        },
                    )
                )
                continue
            if agent_id not in snapshot.actor_ids:
                findings.append(
                    Finding(
                        "dangling_reference",
                        {
                            "table": "agent_toolkit_bindings",
                            "row_id": atb_id,
                            "toolkit_id": toolkit_id,
                            "missing": "actor",
                            "missing_id": agent_id,
                        },
                    )
                )
                continue
            for tcb in tcb_by_toolkit.get(toolkit_id, []):
                credential = snapshot.credentials[tcb.credential_id]
                stamps = [s for s in (atb_bound_at, tcb.bound_at) if s is not None]
                pair = pairs.setdefault(
                    (agent_id, tcb.credential_id),
                    _DerivedPair(agent_id=agent_id, credential_id=tcb.credential_id),
                )
                pair.paths.append(
                    _Path(
                        toolkit_id=toolkit_id,
                        toolkit_name=toolkit.name,
                        toolkit_active=toolkit.active,
                        atb_id=atb_id,
                        tcb_id=tcb.id,
                        bound_at=max(stamps) if stamps else None,
                        rules=snapshot.pair_rules.get((toolkit_id, tcb.credential_id), []),
                        pooled_rules=snapshot.pooled_rules.get(
                            (toolkit_id, credential.api_vendor), []
                        ),
                    )
                )
        return pairs

    @staticmethod
    def _hygiene_findings(snapshot: _Snapshot, findings: list[Finding]) -> None:
        """Report categories independent of the pair derivation."""
        for key in snapshot.toolkit_keys:
            # Hash material (hashed_key, lookup_hash) stays out of the report.
            if not key.revoked and key.migrated_actor_id is None:
                findings.append(
                    Finding(
                        "active_toolkit_key",
                        {
                            "key_id": key.id,
                            "toolkit_id": key.toolkit_id,
                            "label": key.label,
                            "key_preview": key.key_preview,
                            "last_used_at": _iso(key.last_used_at),
                            "created_at": _iso(key.created_at),
                            "remediation": (
                                "revoke the key, or mint the holder a service-account "
                                "key (`retire-toolkit-keys` was removed in Phase 6b)"
                            ),
                        },
                    )
                )
            if key.migrated_actor_id is not None:
                scopes = snapshot.scopes_by_actor.get(key.migrated_actor_id, [])
                excess = sorted(s for s in scopes if s != _EXECUTE_SCOPE)
                if excess:
                    findings.append(
                        Finding(
                            "scope_exceeds_execute",
                            {
                                "service_account_id": key.migrated_actor_id,
                                "key_id": key.id,
                                "toolkit_id": key.toolkit_id,
                                "scopes": sorted(scopes),
                                "excess_scopes": excess,
                            },
                        )
                    )

    @staticmethod
    def _pair_findings(pair: _DerivedPair, findings: list[Finding]) -> None:
        """Conflict / drift / inactive-path report lines for one pair."""
        for path in pair.paths:
            if not path.toolkit_active:
                detail = {
                    "agent_id": pair.agent_id,
                    "credential_id": pair.credential_id,
                    "toolkit_id": path.toolkit_id,
                    "toolkit_name": path.toolkit_name,
                    "agent_toolkit_binding_id": path.atb_id,
                    "toolkit_credential_binding_id": path.tcb_id,
                    "note": (
                        "LIVE access through an inactive toolkit — toolkits.active never "
                        "gated bound agents; migrated, not skipped"
                    ),
                }
                findings.append(Finding("inactive_toolkit_binding", detail))
                logger.warning("toolkit_flattening_inactive_toolkit_binding", **detail)
            # Drift: the pooled (toolkit, vendor) list the legacy broker
            # evaluated is not the per-pair list the direct model enforces.
            if [r.id for r in path.pooled_rules] != [r.id for r in path.rules]:
                findings.append(
                    Finding(
                        "pooled_rule_drift",
                        {
                            "agent_id": pair.agent_id,
                            "credential_id": pair.credential_id,
                            "toolkit_id": path.toolkit_id,
                            "per_pair_rules": [_rule_row(r) for r in path.rules],
                            "pooled_rules": [_rule_row(r) for r in path.pooled_rules],
                        },
                    )
                )
        if pair.conflicting:
            findings.append(
                Finding(
                    "rule_conflict",
                    {
                        "agent_id": pair.agent_id,
                        "credential_id": pair.credential_id,
                        "resolution": "default_deny",
                        "contributing": [
                            {
                                "toolkit_id": path.toolkit_id,
                                "toolkit_name": path.toolkit_name,
                                "rules": [_rule_row(r) for r in path.rules],
                            }
                            for path in pair.paths
                        ],
                    },
                )
            )

    async def run(self, *, diff_only: bool = False) -> FlatteningRunResult:
        """Flatten the toolkit graph (or preview it with ``diff_only``)."""
        snapshot = await self._load_snapshot()
        result = FlatteningRunResult(diff_only=diff_only)
        self._hygiene_findings(snapshot, result.findings)
        pairs = self._derive_pairs(snapshot, result.findings)
        result.pairs_total = len(pairs)

        to_create: list[_DerivedPair] = []
        for key in sorted(pairs):
            pair = pairs[key]
            self._pair_findings(pair, result.findings)
            if key in snapshot.existing_pairs:
                result.already_present += 1
            else:
                to_create.append(pair)

        creation_category = "binding_would_create" if diff_only else "binding_created"

        # Rules control-first: a crash after this transaction leaves inert
        # orphan rule sets, never a live rule-less binding.
        rule_set_ids: dict[tuple[str, str], str | None] = {}
        if not diff_only:
            async with self._ctx.control_db.transaction() as control_session:
                for pair in to_create:
                    if not pair.target_rules:
                        rule_set_ids[(pair.agent_id, pair.credential_id)] = None
                        continue
                    rule_set_ids[(pair.agent_id, pair.credential_id)] = await self._ensure_rule_set(
                        control_session, pair
                    )

        # Bindings admin-last, audit in the same transaction.
        for pair in to_create:
            source = pair.source_path
            rule_set_id = rule_set_ids.get((pair.agent_id, pair.credential_id))
            detail: dict[str, Any] = {
                "agent_id": pair.agent_id,
                "credential_id": pair.credential_id,
                "rule_set_name": (
                    _rule_set_name(source.toolkit_id, pair.credential_id)
                    if pair.target_rules
                    else None
                ),
                "rule_count": len(pair.target_rules),
                "default_deny": not pair.target_rules,
                "bound_at": _iso(pair.bound_at),
                "via_toolkit_ids": sorted(p.toolkit_id for p in pair.paths),
                "source_agent_toolkit_binding_ids": sorted(p.atb_id for p in pair.paths),
                "source_toolkit_credential_binding_ids": sorted(p.tcb_id for p in pair.paths),
            }
            if not diff_only:
                async with self._ctx.admin_db.transaction() as admin_session:
                    binding_id = await FlatteningAdminRepository.insert_credential_binding(
                        admin_session,
                        agent_id=pair.agent_id,
                        credential_id=pair.credential_id,
                        rule_set_id=rule_set_id,
                        bound_at=pair.bound_at,
                    )
                    await record_audit(
                        admin_session,
                        action=AuditAction.GRANT,
                        target_type=AuditTargetType.CREDENTIAL_BINDING,
                        target_id=binding_id,
                        actor_type=_AUDIT_ACTOR_TYPE,
                        actor_id=SYSTEM_ACTOR,
                        target_parent_id=pair.agent_id,
                        reason="theme5_flattening",
                        after=detail,
                        origin=None,
                    )
                detail["binding_id"] = binding_id
                detail["rule_set_id"] = rule_set_id
            result.created += 1
            result.findings.append(Finding(creation_category, detail))
            logger.info("toolkit_flattening_binding", diff_only=diff_only, **detail)

        # Historical-name backfill (Phase 6b prerequisite): denormalize
        # control toolkit names onto admin ``execution_records.toolkit_name``
        # so the read path survives the toolkits drop. Skipped in
        # ``--diff-only`` (it would have to add the column — a write);
        # ``verify`` reports any resolvable rows still missing it.
        if not diff_only:
            result.backfilled_execution_names = await self._backfill_execution_names(snapshot)

        logger.info(
            "toolkit_flattening_run",
            diff_only=diff_only,
            pairs_total=result.pairs_total,
            created=result.created,
            already_present=result.already_present,
            backfilled_execution_names=result.backfilled_execution_names,
            findings=len(result.findings),
        )
        return result

    async def _backfill_execution_names(self, snapshot: _Snapshot) -> int:
        """Copy toolkit names onto unnamed historical execution rows.

        App-level cross-DB (control names were loaded in the snapshot; the
        writes go to admin), batched per distinct toolkit id. Rows whose
        toolkit no longer exists keep NULL — exactly what the old read-time
        resolver reported for them.
        """
        total = 0
        async with self._ctx.admin_db.transaction() as admin_session:
            await FlatteningAdminRepository.ensure_execution_toolkit_name_column(admin_session)
            toolkit_ids = await FlatteningAdminRepository.list_unbackfilled_toolkit_ids(
                admin_session
            )
            for toolkit_id in toolkit_ids:
                toolkit = snapshot.toolkits.get(toolkit_id)
                if toolkit is None:
                    continue
                total += await FlatteningAdminRepository.backfill_execution_toolkit_name(
                    admin_session, toolkit_id=toolkit_id, name=toolkit.name
                )
        return total

    @staticmethod
    async def _ensure_rule_set(
        # ``Any`` because the arch rule forbids sqlalchemy imports in control
        # services (tests/arch/test_no_direct_db.py); the repos it's passed to
        # type it as AsyncSession.
        session: Any,
        pair: _DerivedPair,
    ) -> str:
        """Copy the pair's effective rules into a shared rule set.

        Named per source ``(toolkit, credential)`` so every agent flattened
        off the same toolkit pair shares one set, and a re-run reuses it. An
        existing set is never overwritten (operator edits win).
        """
        name = _rule_set_name(pair.source_path.toolkit_id, pair.credential_id)
        existing = await PermissionRuleSetRepository.get_by_name(session, name)
        if existing is not None:
            return existing.id
        rule_set = await PermissionRuleSetRepository.create(
            session,
            name=name,
            description=(
                f"Rules of toolkit {pair.source_path.toolkit_id} / credential "
                f"{pair.credential_id}, copied by the theme-5 Phase 6a flattening job"
            ),
            created_by=SYSTEM_ACTOR,
        )
        await PermissionRuleSetRepository.replace_user_rules(
            session,
            rule_set.id,
            [
                {
                    "effect": rule.effect,
                    "methods": rule.methods,
                    "path": rule.path,
                    "match_mode": rule.match_mode,
                    "operations": rule.operations,
                    "comment": rule.comment,
                }
                for rule in pair.target_rules
            ],
            created_by=SYSTEM_ACTOR,
        )
        return rule_set.id

    async def verify(self, *, acknowledge: bool = False) -> VerificationResult:
        """Run the R-02 verification queries; optionally write the 6b gate row.

        Passes iff every legacy ``(agent, credential)`` pair exists as a
        direct binding — extra hand-created direct pairs are fine (upgraded
        installs kept binding post-Phase-1). Per-pair rule-list divergence is
        a report entry, not a failure. The acknowledgement sentinel is
        written only when ``acknowledge`` is set AND this verification
        passed — never from any other code path.
        """
        snapshot = await self._load_snapshot()
        findings: list[Finding] = []
        self._hygiene_findings(snapshot, findings)
        pairs = self._derive_pairs(snapshot, findings)

        missing = [key for key in sorted(pairs) if key not in snapshot.existing_pairs]
        for agent_id, credential_id in missing:
            pair = pairs[(agent_id, credential_id)]
            findings.append(
                Finding(
                    "verify_missing_binding",
                    {
                        "agent_id": agent_id,
                        "credential_id": credential_id,
                        "via_toolkit_ids": sorted(p.toolkit_id for p in pair.paths),
                    },
                )
            )

        # Historical-name coverage: execution rows whose toolkit still exists
        # in control but whose denormalized name is missing would lose their
        # name forever at the 6b drop — fail verification (remediation: run
        # the flatten job on this release; its backfill step fills them).
        # Rows whose toolkit is already gone are unresolvable either way and
        # do not block.
        async with self._ctx.admin_db.session() as admin_session:
            unbackfilled_ids = await FlatteningAdminRepository.list_unbackfilled_toolkit_ids(
                admin_session
            )
        unbackfilled = sorted(t for t in unbackfilled_ids if t in snapshot.toolkits)
        if unbackfilled:
            findings.append(
                Finding(
                    "verify_unbackfilled_execution_names",
                    {
                        "toolkit_ids": unbackfilled,
                        "remediation": (
                            "run `jentic_one flatten-toolkits` on this release; its "
                            "backfill step denormalizes toolkit names onto "
                            "execution_records before the drop"
                        ),
                    },
                )
            )

        async with self._ctx.control_db.session() as control_session:
            for key in sorted(pairs):
                if key in snapshot.existing_pairs:
                    await self._compare_pair_rules(
                        control_session,
                        pairs[key],
                        rule_set_id=snapshot.existing_pairs[key],
                        findings=findings,
                    )

        result = VerificationResult(
            passed=not missing and not unbackfilled,
            legacy_pair_count=len(pairs),
            direct_binding_count=len(snapshot.existing_pairs),
            missing_pair_count=len(missing),
            unbackfilled_execution_name_count=len(unbackfilled),
            findings=findings,
        )
        findings.append(
            Finding(
                "verify_summary",
                {
                    "passed": result.passed,
                    "legacy_pair_count": result.legacy_pair_count,
                    "direct_binding_count": result.direct_binding_count,
                    "missing_pair_count": result.missing_pair_count,
                    "unbackfilled_execution_name_count": (result.unbackfilled_execution_name_count),
                    "tool_version": __version__,
                },
            )
        )
        logger.info(
            "toolkit_flattening_verify",
            passed=result.passed,
            legacy_pair_count=result.legacy_pair_count,
            direct_binding_count=result.direct_binding_count,
            missing_pair_count=result.missing_pair_count,
            unbackfilled_execution_name_count=result.unbackfilled_execution_name_count,
        )

        if acknowledge and result.passed:
            async with self._ctx.control_db.transaction() as control_session:
                ack = await FlatteningControlRepository.record_acknowledgement(
                    control_session,
                    acknowledged_at=dt.datetime.now(dt.UTC),
                    legacy_pair_count=result.legacy_pair_count,
                    direct_binding_count=result.direct_binding_count,
                    report_finding_count=len(result.findings),
                    tool_version=__version__,
                )
                ack_id = ack.id
            result.acknowledged = True
            logger.info("toolkit_flattening_acknowledged", ack_id=ack_id)
        elif acknowledge:
            logger.warning(
                "toolkit_flattening_acknowledge_refused",
                detail="verification failed; sentinel not written",
                missing_pair_count=result.missing_pair_count,
            )
        return result

    @staticmethod
    async def _compare_pair_rules(
        # ``Any`` for the same arch-rule reason as ``_ensure_rule_set``.
        control_session: Any,
        pair: _DerivedPair,
        *,
        rule_set_id: str | None,
        findings: list[Finding],
    ) -> None:
        """Ordered rule-list equality for one pair (report entry on mismatch).

        Expected is what the flattening semantics produce (the identical
        contributors' list, or empty on conflict); actual is the direct
        binding's effective list — its rule set when it has one, its inline
        ``agent_permission_rules`` otherwise.
        """
        if rule_set_id is not None:
            actual_rules: list[Any] = await PermissionRuleSetRepository.list_rules(
                control_session, rule_set_id
            )
        else:
            actual_rules = await FlatteningControlRepository.list_inline_rules(
                control_session, agent_id=pair.agent_id, credential_id=pair.credential_id
            )
        expected = [_rule_semantics(r) for r in pair.target_rules]
        actual = [_rule_semantics(r) for r in actual_rules]
        if expected == actual:
            return
        findings.append(
            Finding(
                "verify_rule_mismatch",
                {
                    "agent_id": pair.agent_id,
                    "credential_id": pair.credential_id,
                    "rule_set_id": rule_set_id,
                    "legacy_conflict": pair.conflicting,
                    "expected_rules": [_rule_row(r) for r in pair.target_rules],
                    "actual_rules": [
                        {
                            "id": r.id,
                            "effect": r.effect,
                            "methods": r.methods,
                            "path": r.path,
                            "match_mode": r.match_mode,
                            "operations": r.operations,
                            "is_system": r.is_system,
                            "comment": r.comment,
                            "sequence": r.sequence,
                        }
                        for r in actual_rules
                    ],
                },
            )
        )
