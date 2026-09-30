"""Theme-8 — the service-account → agent migration and retirement.

Converts every service account into a successor **agent** (copy-then-sweep,
N1): the SA's stored scope grants, credential bindings, and API-key
digest are COPIED onto a raw-SQL-minted successor agent (the digest only
matters for a retired ``jntc_live_`` toolkit key the theme-5 retirement
converted into the SA — ``sak_`` keys stopped authenticating in 0.41 and the
resolver refuses them by prefix), the SA's opaque
sessions are revoked (H-1), and the row is stamped
(``migrated_to_actor_id`` + ``migrated_at``) — all in one admin transaction
per SA, with per-SA audit rows under the system actor (F4). The control-DB
half — the copy of the ``sva_``-keyed per-binding inline permission rules
(H2) — runs in its own transaction BEFORE the admin one, keyed on the
successor id planned for it: rules on a binding that does not exist yet are
inert, so a failed admin step grants nothing (the pre-copied rows are then
discarded), and the copy only ever lands on bindings this run creates. A
control-DB failure is reported as a ``failed`` row before anything is
written on the admin side (L1); the next run migrates the row afresh.

**No resurrection of removed access.** Nothing is ever copied onto an
existing successor grant, binding, or binding rule list: an operator may have
narrowed it on purpose (a binding emptied of rules, a scope removed), and the
data cannot tell "removed" from "never copied". Where the retirement
therefore leaves something uncopied it reports a WARNING line (service
account, successor, what was not copied) in the retirement summary so the
operator can re-grant it.

Disposition (OQ-1, rev 5): ``active`` → full migration (successor
``active``); ``disabled`` → full migration (successor ``disabled``, NF-2);
``pending``/``rejected``/``archived`` → skip-but-stamp (no successor; stamp
value ``skipped``). Successor creation is raw SQL — never
``AgentService.create()``/``approve()`` (F1: both default-grant
``DEFAULT_AGENT_SCOPES``; a zero-grant SA must yield a zero-grant
successor).

**Retirement (theme-8 Phase 4).** :meth:`ServiceAccountMigrationService.retire`
is the only entry point: the migration runner (``python -m
jentic_one.migrations.run``) calls it on a full upgrade, after the admin DB
reaches ``d1e2f3a4b5c6`` and before it applies the ``e2f3a4b5c6d7`` drop. It
migrates whatever is still unstamped, copies onto earlier successors any SA
grant or binding created after their stamp that the successor never held
(the audit trail shows no removal of it from the successor), **verifies**
(every SA stamped, no failed row, grant and binding twins present, exact
inline-rule parity for this run's migrations) and only on a clean
verification sweeps the SA-side originals and deletes the remaining
``sva_``-keyed control-DB rules. It then WARNS (log line + the runner's
stdout) with every service account → successor agent id, because the
accounts' ``sak_`` keys no longer work. A failed verification
raises :class:`ServiceAccountRetirementError` naming every failing SA before
anything is swept; every step is idempotent, so a re-run after the fix is
safe.

Concurrency: the retirement holds a control-DB run lock; each SA migrates in
one admin transaction (``BEGIN IMMEDIATE`` on SQLite), with a pg advisory-lock
fast path, an in-transaction stamp re-check, and — the real backstop — the
``uq_agent_credentials_api_key_hash`` unique partial index: a losing
double-mint fails its whole transaction and is reported, never a partial
write.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Collection, Iterable, Mapping
from dataclasses import asdict, dataclass, field, replace
from typing import Any

import structlog

from jentic_one.control.repos.agent_permission_rule_repo import AgentPermissionRuleRepository
from jentic_one.control.repos.service_account_migration_repo import (
    ADMIN_LEVEL_SCOPES,
    SKIPPED_STAMP,
    SYSTEM_ACTOR,
    ServiceAccountMigrationRepository,
)
from jentic_one.control.services.run_lock import SA_RETIREMENT_LOCK_KEY, hold_run_lock
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit
from jentic_one.shared.context import Context
from jentic_one.shared.db.errors import DatabaseIntegrityError
from jentic_one.shared.models import ActorStatus, Origin

logger = structlog.get_logger(__name__)

#: IMPL-DECISION 5 — deviation, documented: the guide recommends
#: ``actor_type="system"``, but the ``test_no_system_actor`` arch test forbids
#: the bare literal ``"system"`` anywhere in src. The in-code precedent for
#: job-derived audit rows is ``toolkit_flattening._AUDIT_ACTOR_TYPE ==
#: "system:job"`` — follow it. Never an ``ActorType`` member (jobs are not
#: authenticated actors); the audit read surface treats the column as an
#: opaque string.
_AUDIT_ACTOR_TYPE = "system:job"
#: Kept from the 0.40 job so every migration audit row — boot job, CLI, or
#: this retirement — answers the same ``actor_id`` query.
_AUDIT_ACTOR_ID = "migrate-service-accounts"

#: Outcomes of :meth:`ServiceAccountMigrationService.run` that created a
#: successor in THIS run (its copies are verified exactly, not just for gaps).
_MIGRATED_NOW = frozenset({"migrated", "migrated-disabled"})

_RETIREMENT_RERUN_STEP = (
    "Nothing was swept or dropped. Fix the listed rows, then re-run "
    "`python -m jentic_one.migrations.run` (safe to repeat). See 'Upgrading to "
    "0.41.0' in docs/development/releasing.md."
)


#: Report-field → log-key renames. The central redactor
#: (``shared/redaction.py``) blanks any key containing ``secret``,
#: ``credential``, or ``_token`` — which would hide the operator signal
#: (OQ-1: who still holds client_credentials) behind ``***REDACTED***``. The
#: redaction rule prescribes renaming false positives rather than weakening
#: the redactor, so the structured-log lines use these non-matching keys.
_LOG_KEY_RENAMES: dict[str, str] = {
    "had_client_secret": "cc_holder",
    "credential_binding_count": "cred_binding_count",
    "access_tokens_revoked": "access_revoked_count",
    "refresh_tokens_revoked": "refresh_revoked_count",
}


def _log_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Rename redactor-matching report keys for a structured-log line."""
    return {_LOG_KEY_RENAMES.get(key, key): value for key, value in fields.items()}


_ADMIN_SCOPE_REVIEW_STEP = (
    "Review whether the successor agent should keep this scope; if not, remove it "
    "with `PUT /agents/{agent_id}/scopes` (see the upgrade notes for the audit queries)."
)


def _admin_level_grants(grants: list[tuple[str, str | None]]) -> tuple[dict[str, Any], ...]:
    """The admin-level subset of copied ``(scope, original granted_by)`` pairs."""
    return tuple(
        {"scope": scope, "original_granted_by": granted_by}
        for scope, granted_by in grants
        if scope in ADMIN_LEVEL_SCOPES
    )


class _ConcurrentWinnerError(Exception):
    """A concurrent run stamped this SA first — roll back, report already_migrated."""


class _ServiceAccountVanishedError(Exception):
    """The SA row listed by ``run()`` no longer exists at migration time."""


@dataclass(frozen=True)
class RetirementProblem:
    """One reason a service account blocks the retirement."""

    service_account_id: str
    reason: str


class ServiceAccountRetirementError(Exception):
    """The pre-drop verification failed; nothing was swept or dropped."""

    def __init__(self, problems: Iterable[RetirementProblem]) -> None:
        self.problems: tuple[RetirementProblem, ...] = tuple(problems)
        self.service_account_ids: tuple[str, ...] = tuple(
            sorted({p.service_account_id for p in self.problems})
        )
        details = "; ".join(f"{p.service_account_id}: {p.reason}" for p in self.problems)
        super().__init__(
            "Refusing to retire the service accounts (theme-8 Phase 4): "
            f"{len(self.service_account_ids)} service account(s) failed verification "
            f"({', '.join(self.service_account_ids)}) — {details}. {_RETIREMENT_RERUN_STEP}"
        )


@dataclass(frozen=True)
class ServiceAccountMigrationOutcome:
    """What happened to one service account (one structured-log line)."""

    service_account_id: str
    outcome: str  # migrated | migrated-disabled | skipped-non-active |
    #             # already_migrated | failed
    successor_agent_id: str | None = None
    #: What this run copied/revoked.
    stored_scope_count: int = 0
    credential_binding_count: int = 0
    #: Control-DB ``agent_permission_rules`` rows copied sva_ → agnt_ (H2).
    permission_rule_count: int = 0
    access_tokens_revoked: int = 0
    refresh_tokens_revoked: int = 0
    #: OQ-1 — the SA held a client secret (client_credentials): that grant
    #: does not carry over to the successor.
    had_client_secret: bool = False
    #: OQ-5: the successor agent is visible to ``owner:agents:read`` holders
    #: via ``parent_actor_id=owner_id``, where the SA was governed by
    #: ``owner:service-accounts:read``. The note also names the other
    #: behaviour changes an operator must review per SA: (a) control-DB
    #: objects ``created_by`` the ``sva_`` id are NOT re-attributed, so the
    #: successor loses owner-scoped access to them; (b) with
    #: ``parent_actor_id=owner`` any copied ``owner:*`` delegation scope now
    #: widens to the owner's resources; (c) ``sak_`` keys no longer
    #: authenticate (0.41): callers need a ``jak_`` key for the successor,
    #: and as an agent ``POST /oauth/mint`` is gone (404),
    #: ``/integrations:connect`` refuses ``agent_id`` in the body, and an
    #: agent-initiated connect session cannot be confirmed by the agent itself
    #: (``_forbid_self_confirm``).
    owner_visibility_note: str | None = None
    reason: str | None = None
    #: The scope names copied onto the successor.
    copied_scopes: tuple[str, ...] = ()
    #: The admin-level subset of ``copied_scopes`` (``ADMIN_LEVEL_SCOPES``),
    #: each with the SA grant's ORIGINAL ``granted_by`` (the successor's twin
    #: is stamped with the job's system actor). Informational: the grants are
    #: carried over unchanged; the operator reviews them.
    admin_level_scopes: tuple[dict[str, Any], ...] = ()


@dataclass
class SweepOutcome:
    """Result of one sweep pass."""

    swept: list[str] = field(default_factory=list)
    #: Opaque SA sessions revoked by the sweep (M1).
    access_tokens_revoked: int = 0
    refresh_tokens_revoked: int = 0
    #: Control-DB ``sva_``-keyed inline rules deleted (H2).
    permission_rules_deleted: int = 0


@dataclass
class RetirementOutcome:
    """What :meth:`ServiceAccountMigrationService.retire` did."""

    #: ``no_tables`` (already dropped — nothing to do) | ``retired``
    action: str
    migrated: int = 0
    skipped: int = 0
    already_migrated: int = 0
    #: Twins copied onto earlier successors for SA rows created after their stamp.
    post_stamp_grants_copied: int = 0
    post_stamp_bindings_copied: int = 0
    #: Inline rules copied onto those post-stamp binding twins.
    post_stamp_permission_rules_copied: int = 0
    swept: int = 0
    access_tokens_revoked: int = 0
    refresh_tokens_revoked: int = 0
    permission_rules_deleted: int = 0
    #: Control-DB ``sva_`` rules left behind by ``sva_`` ids with no SA row.
    orphan_permission_rules_deleted: int = 0
    #: Every retired service account → its successor agent id (``None``: a
    #: skip-stamped account, which got no successor). Ids only, never secrets.
    successors: dict[str, str | None] = field(default_factory=dict)
    #: One line per grant, binding, or binding rule list NOT copied to a
    #: successor because that could have resurrected removed access — the
    #: operator re-grants what is still needed. Ids and scope names only.
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RetirementWarning:
    """Something the retirement deliberately did not copy to a successor."""

    service_account_id: str
    successor_agent_id: str
    #: e.g. ``scope grant 'x'`` / ``credential binding cred_…``.
    not_copied: str
    reason: str

    def line(self) -> str:
        return (
            f"{self.service_account_id}: {self.not_copied}: NOT copied to successor agent "
            f"{self.successor_agent_id} ({self.reason}); re-grant it if the agent still needs it"
        )


#: The operator warning printed (and logged) after a retirement that retired
#: at least one service account.
SAK_KEYS_RETIRED_WARNING = (
    "service-account (sak_) keys no longer authenticate: each service account "
    "below was migrated to the listed successor agent. Mint a jak_ key for that "
    "agent and switch its callers to it. (A retired jntc_live_ toolkit key that "
    "had been converted into one of these accounts keeps working, as the "
    "successor agent, until at least 2026-12-01.)"
)


def rule_parity_problems(
    counts: Mapping[tuple[str, str], int],
    successor_of: Mapping[str, str],
    migrated_now: Iterable[str],
) -> list[RetirementProblem]:
    """Exact inline-rule parity for every SA migrated in THIS run.

    ``counts`` is ``{(actor_id, credential_id): rule count}`` over the SA and
    successor ids. The copy started from an empty successor, so it must hold
    the same count per binding. SAs stamped by an earlier run are not checked
    here (see :func:`rule_parity_warnings`): the operator may have edited the
    successor's list since.
    """
    exact = set(migrated_now)
    problems: list[RetirementProblem] = []
    for (actor_id, credential_id), n in sorted(counts.items()):
        successor = successor_of.get(actor_id)
        if successor is None or actor_id not in exact:
            continue  # a successor-side row, skip-stamped / unknown, or earlier run
        held = counts.get((successor, credential_id), 0)
        if held != n:
            problems.append(
                RetirementProblem(
                    actor_id,
                    f"successor {successor} holds {held} inline permission rule(s) for "
                    f"credential {credential_id}, expected {n}",
                )
            )
    return problems


def rule_parity_warnings(
    counts: Mapping[tuple[str, str], int],
    successor_of: Mapping[str, str],
    migrated_now: Iterable[str],
    *,
    skip: Collection[tuple[str, str]] = (),
) -> list[RetirementWarning]:
    """SA bindings (earlier runs) whose successor holds NO inline rules.

    Not re-copied: an empty successor list may have been emptied on purpose,
    and nothing records whether it was. Reported so the operator can decide.
    ``skip`` holds ``(sa_id, credential_id)`` pairs already reported.
    """
    exact = set(migrated_now)
    warnings: list[RetirementWarning] = []
    for (actor_id, credential_id), n in sorted(counts.items()):
        successor = successor_of.get(actor_id)
        if successor is None or actor_id in exact or (actor_id, credential_id) in skip:
            continue
        if counts.get((successor, credential_id), 0) == 0:
            warnings.append(
                RetirementWarning(
                    actor_id,
                    successor,
                    f"{n} inline permission rule(s) for credential {credential_id}",
                    "the successor's rule list for that binding is empty, possibly on purpose",
                )
            )
    return warnings


@dataclass
class _PostStampCopy:
    """What :meth:`ServiceAccountMigrationService._copy_post_stamp_rows` did."""

    grants: int = 0
    bindings: int = 0
    rules: int = 0
    withheld: list[RetirementWarning] = field(default_factory=list)
    #: ``(kind, sa_id, scope|credential_id)`` of every withheld row.
    _keys: set[tuple[str, str, str]] = field(default_factory=set)

    def withhold(self, warning: RetirementWarning, key: tuple[str, str, str]) -> None:
        self.withheld.append(warning)
        self._keys.add(key)

    @property
    def withheld_keys(self) -> frozenset[tuple[str, str, str]]:
        return frozenset(self._keys)


class ServiceAccountMigrationService:
    """Orchestrates the migration, the sweep, and the pre-drop retirement."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def tables_present(self) -> bool:
        """Whether the service-account tables still exist (pre-Phase-4 drop)."""
        async with self._ctx.admin_db.session() as session:
            return await ServiceAccountMigrationRepository.tables_present(session)

    async def run(self) -> list[ServiceAccountMigrationOutcome]:
        """Migrate every service account; return one outcome per SA."""
        async with self._ctx.admin_db.session() as session:
            rows = await ServiceAccountMigrationRepository.list_service_accounts(session)

        outcomes: list[ServiceAccountMigrationOutcome] = []
        for row in rows:
            outcome = await self._migrate_one(row)
            outcomes.append(outcome)
            logger.info("service_account_migration", **_log_fields(asdict(outcome)))
        return outcomes

    @staticmethod
    def _disposition(status: str) -> tuple[str, str | None]:
        """OQ-1 switch: (outcome label, successor status or None for skip)."""
        if status == ActorStatus.ACTIVE:
            return "migrated", ActorStatus.ACTIVE.value
        if status == ActorStatus.DISABLED:
            return "migrated-disabled", ActorStatus.DISABLED.value
        return "skipped-non-active", None

    @staticmethod
    def _visibility_note(row: Any) -> str:
        """Per-SA review note — see the field comment on ``owner_visibility_note``."""
        return (
            f"successor agent becomes visible to owner:agents:read via"
            f" parent_actor_id={row.owner_id} (OQ-5, report-only); any copied owner:*"
            f" scope now also reaches that owner's resources; control-DB objects"
            f" created_by {row.id} are not re-attributed (the successor loses"
            f" owner-scoped access to them); sak_ keys no longer authenticate"
            f" (0.41) — callers need a jak_ key for the successor, and as an"
            f" agent: POST /oauth/mint is gone (404), /integrations:connect refuses agent_id"
            f" in the body, and agent-initiated connect sessions cannot be"
            f" self-confirmed"
        )

    async def _precopy_rules(self, service_account_id: str, agent_id: str) -> int:
        """Control-DB half of one SA migration, BEFORE the admin transaction.

        Copies the ``sva_``-keyed per-binding inline permission rules (H2)
        onto the successor id planned for this run. (The M-E
        ``toolkit_keys.migrated_actor_id`` re-stamp was deleted with that
        table in theme-5 Phase 6b.) The two DBs cannot share a transaction;
        copying first means the rules can only ever land on bindings the
        admin transaction is about to create — a crash in between leaves
        inert rows keyed on an agent id that never came to exist, never a
        successor whose rules were lost or re-copied later.
        """
        if not self._ctx.has_db("control"):
            return 0
        async with self._ctx.control_db.transaction() as control_session:
            return await ServiceAccountMigrationRepository.copy_permission_rules(
                control_session, service_account_id=service_account_id, agent_id=agent_id
            )

    async def _discard_precopied_rules(self, agent_id: str) -> None:
        """Best-effort delete of rules pre-copied for a successor never created.

        Inert either way (no agent, no binding carries that id) — a failure is
        logged, never raised.
        """
        try:
            async with self._ctx.control_db.transaction() as control_session:
                await AgentPermissionRuleRepository.delete_for_agent(control_session, agent_id)
        except Exception as exc:
            logger.warning(
                "service_account_migration_precopy_discard_failed",
                planned_agent_id=agent_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )

    async def _migrate_one(self, row: Any) -> ServiceAccountMigrationOutcome:
        """Control-DB inline-rule pre-copy, then copy → revoke → stamp → audit
        in one admin transaction."""
        if row.migrated_to_actor_id is not None:
            # Nothing to redo: the control-DB rule copy ran before the stamp
            # committed. Re-copying onto the existing successor could refill a
            # rule list the operator emptied on purpose.
            return ServiceAccountMigrationOutcome(
                service_account_id=row.id,
                outcome="already_migrated",
                successor_agent_id=(
                    None if row.migrated_to_actor_id == SKIPPED_STAMP else row.migrated_to_actor_id
                ),
                had_client_secret=row.client_secret_hash is not None,
            )

        # Control DB first (L1): pre-copy the inline rules onto the planned
        # successor id. A failure here is a row outcome with nothing written
        # on the admin side; the next run migrates the row afresh.
        planned_id = ServiceAccountMigrationRepository.new_successor_agent_id()
        try:
            rules_copied = await self._precopy_rules(row.id, planned_id)
        except Exception as exc:
            logger.warning(
                "service_account_migration_control_sync_failed",
                service_account_id=row.id,
                error=str(exc),
                error_type=type(exc).__name__,
                actionable_step=(
                    "Nothing was written for this service account; re-run "
                    "`python -m jentic_one.migrations.run` once the control DB is reachable."
                ),
            )
            return ServiceAccountMigrationOutcome(
                service_account_id=row.id,
                outcome="failed",
                had_client_secret=row.client_secret_hash is not None,
                reason=f"control_sync_error:{type(exc).__name__}",
            )

        outcome = await self._migrate_admin(row, planned_id)
        if outcome.successor_agent_id != planned_id:
            # No successor was created under the planned id (skip
            # disposition, concurrent winner, rolled-back transaction): the
            # pre-copied rules are orphaned — inert, but tidy them up.
            if rules_copied:
                await self._discard_precopied_rules(planned_id)
            return outcome
        return replace(outcome, permission_rule_count=rules_copied)

    async def _migrate_admin(self, row: Any, planned_id: str) -> ServiceAccountMigrationOutcome:
        """Copy → revoke → stamp → audit for one SA, one admin transaction.

        H1: disposition, digest, owner, and name are derived from the row
        re-read INSIDE the per-SA transaction, never from the ``run()``
        snapshot — a disable or key rotation landing between the list and
        this transaction must be reflected in the successor.
        """
        current: Any = row
        label = "failed"
        successor_status: str | None = None
        agent_id: str | None = None
        stored_scopes = credential_bindings = 0
        copied: list[tuple[str, str | None]] = []
        access_revoked = refresh_revoked = 0

        try:
            async with self._ctx.admin_db.transaction() as session:
                await ServiceAccountMigrationRepository.acquire_migration_lock(session, row.id)
                # Stamp time is taken only once the lock is held: rows the
                # previous holder committed while we waited predate the stamp
                # and must not read as post-stamp mutations in verify
                # criterion 5.
                now = dt.datetime.now(dt.UTC)
                # In-transaction re-read (FOR UPDATE OF sa on pg; SQLite holds
                # the BEGIN IMMEDIATE write lock). It serialises with the
                # service-layer stamp guards and key rotation, which lock the
                # same row, and doubles as the stamp re-check: a concurrent
                # winner is a clean no-op, never a double mint.
                fresh = await ServiceAccountMigrationRepository.get_service_account(
                    session, row.id, for_update=True
                )
                if fresh is None:
                    raise _ServiceAccountVanishedError(row.id)
                if fresh.migrated_to_actor_id is not None:
                    raise _ConcurrentWinnerError(fresh.migrated_to_actor_id)
                current = fresh
                label, successor_status = self._disposition(current.status)

                if successor_status is not None:
                    agent_id = await ServiceAccountMigrationRepository.create_successor_agent(
                        session,
                        service_account_id=current.id,
                        sa_name=current.name,
                        owner_id=current.owner_id,
                        status=successor_status,
                        api_key_hash=current.api_key_hash,
                        agent_id=planned_id,
                    )
                    copied = await ServiceAccountMigrationRepository.copy_scope_grants(
                        session, service_account_id=row.id, agent_id=agent_id
                    )
                    stored_scopes = len(copied)
                    credential_bindings = await ServiceAccountMigrationRepository.copy_bindings(
                        session, service_account_id=row.id, agent_id=agent_id
                    )

                # M5: family-revoke for EVERY disposition, skip-but-stamp
                # included — verify criterion 3 counts live tokens on all SA
                # rows, and a skip-stamped row has no other revocation path.
                (
                    access_revoked,
                    refresh_revoked,
                ) = await ServiceAccountMigrationRepository.revoke_tokens(
                    session, service_account_id=row.id, now=now
                )

                stamped = await ServiceAccountMigrationRepository.stamp(
                    session,
                    service_account_id=row.id,
                    value=agent_id if agent_id is not None else SKIPPED_STAMP,
                    now=now,
                )
                if not stamped:
                    raise _ConcurrentWinnerError(row.id)

                # Audit rows (F4), same transaction, system actor
                # (IMPL-DECISION 5). ARCHIVE is written by the sweep, not here.
                if agent_id is not None:
                    await record_audit(
                        session,
                        action=AuditAction.REGISTER,
                        target_type=AuditTargetType.AGENT,
                        target_id=agent_id,
                        actor_type=_AUDIT_ACTOR_TYPE,
                        actor_id=_AUDIT_ACTOR_ID,
                        after={
                            "service_account_id": row.id,
                            "status": successor_status,
                        },
                        reason="theme8_sa_migration",
                        origin=Origin.SYSTEM.value,
                    )
                    await record_audit(
                        session,
                        action=AuditAction.GRANT,
                        target_type=AuditTargetType.AGENT,
                        target_id=agent_id,
                        actor_type=_AUDIT_ACTOR_TYPE,
                        actor_id=_AUDIT_ACTOR_ID,
                        after={
                            "copied_scope_count": stored_scopes,
                            "copied_scopes": [scope for scope, _ in copied],
                            "admin_level_scopes": sorted(
                                scope for scope, _ in copied if scope in ADMIN_LEVEL_SCOPES
                            ),
                        },
                        reason="theme8_sa_migration_grant_copy",
                        origin=Origin.SYSTEM.value,
                    )
                if agent_id is not None or access_revoked or refresh_revoked:
                    # REVOKE row also covers skip-but-stamp rows that held
                    # live tokens (M5) — the revocation must be attributable.
                    await record_audit(
                        session,
                        action=AuditAction.REVOKE,
                        target_type=AuditTargetType.SERVICE_ACCOUNT,
                        target_id=row.id,
                        actor_type=_AUDIT_ACTOR_TYPE,
                        actor_id=_AUDIT_ACTOR_ID,
                        after={
                            "access_tokens_revoked": access_revoked,
                            "refresh_tokens_revoked": refresh_revoked,
                        },
                        reason="theme8_sa_migration_token_sweep",
                        origin=Origin.SYSTEM.value,
                    )
        except _ServiceAccountVanishedError:
            return ServiceAccountMigrationOutcome(
                service_account_id=row.id,
                outcome="failed",
                had_client_secret=row.client_secret_hash is not None,
                reason="service_account_not_found",
            )
        except _ConcurrentWinnerError:
            return ServiceAccountMigrationOutcome(
                service_account_id=row.id,
                outcome="already_migrated",
                had_client_secret=row.client_secret_hash is not None,
                reason="concurrent_run_won",
            )
        except DatabaseIntegrityError as exc:
            # Most likely uq_agent_credentials_api_key_hash: a concurrent
            # double-mint lost, or the digest already lives on some agent.
            # The transaction rolled back whole — never a partial write.
            logger.warning(
                "service_account_migration_row_failed",
                service_account_id=row.id,
                error=str(exc),
            )
            return ServiceAccountMigrationOutcome(
                service_account_id=row.id,
                outcome="failed",
                had_client_secret=row.client_secret_hash is not None,
                reason="integrity_error",
            )
        except Exception as exc:  # L2: per-row isolation
            # One malformed row must not abort the whole run: the transaction
            # rolled back whole, report it and let the loop continue.
            logger.warning(
                "service_account_migration_row_failed",
                service_account_id=row.id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return ServiceAccountMigrationOutcome(
                service_account_id=row.id,
                outcome="failed",
                had_client_secret=row.client_secret_hash is not None,
                reason=f"error:{type(exc).__name__}",
            )

        admin_level = _admin_level_grants(copied)
        for grant in admin_level:
            # Committed above: one WARNING per admin-level grant the successor
            # now holds, so the operator reviews it (the grant is kept).
            logger.warning(
                "service_account_migration_admin_scope_copied",
                service_account_id=current.id,
                successor_agent_id=agent_id,
                owner_id=current.owner_id,
                scope=grant["scope"],
                original_granted_by=grant["original_granted_by"],
                actionable_step=_ADMIN_SCOPE_REVIEW_STEP,
            )

        reason = f"status={current.status}" if label == "skipped-non-active" else None
        return ServiceAccountMigrationOutcome(
            service_account_id=current.id,
            outcome=label,
            successor_agent_id=agent_id,
            stored_scope_count=stored_scopes,
            credential_binding_count=credential_bindings,
            access_tokens_revoked=access_revoked,
            refresh_tokens_revoked=refresh_revoked,
            had_client_secret=current.client_secret_hash is not None,
            owner_visibility_note=(
                self._visibility_note(current) if agent_id is not None else None
            ),
            reason=reason,
            copied_scopes=tuple(scope for scope, _ in copied),
            admin_level_scopes=admin_level,
        )

    # ------------------------------------------------------------------ sweep

    async def sweep(self) -> SweepOutcome:
        """W3 — delete SA-keyed originals for stamped rows, archive the SA.

        Called only by :meth:`retire`, after a clean verification. Per SA, one
        admin transaction deletes the SA-keyed grant and binding rows, NULLs
        the SA-side digest, revokes every outstanding opaque SA session (M1),
        and archives the row. A control-DB pass then deletes the
        ``sva_``-keyed inline permission rules (H2) for every stamped SA —
        including rows whose admin side a previous, interrupted sweep already
        finished.
        """
        async with self._ctx.admin_db.session() as session:
            rows = await ServiceAccountMigrationRepository.list_sweepable(session)
            # Snapshotted up-front with ``rows`` so the control pass never
            # reaches a row stamped after the admin pass was planned.
            stamps = await ServiceAccountMigrationRepository.list_stamped(session)

        outcome = SweepOutcome()
        for row in rows:
            async with self._ctx.admin_db.transaction() as session:
                archived = await ServiceAccountMigrationRepository.sweep_service_account(
                    session, service_account_id=row.id
                )
                # M1: revoke whatever SA sessions were minted since the
                # migration-time revoke, in the same transaction that archives
                # the row (after which the grant refuses them).
                (
                    access_revoked,
                    refresh_revoked,
                ) = await ServiceAccountMigrationRepository.revoke_tokens(
                    session, service_account_id=row.id, now=dt.datetime.now(dt.UTC)
                )
                if access_revoked or refresh_revoked:
                    await record_audit(
                        session,
                        action=AuditAction.REVOKE,
                        target_type=AuditTargetType.SERVICE_ACCOUNT,
                        target_id=row.id,
                        actor_type=_AUDIT_ACTOR_TYPE,
                        actor_id=_AUDIT_ACTOR_ID,
                        after={
                            "access_tokens_revoked": access_revoked,
                            "refresh_tokens_revoked": refresh_revoked,
                        },
                        reason="theme8_sa_migration_sweep_token_revoke",
                        origin=Origin.SYSTEM.value,
                    )
                # L3: the archive UPDATE is guarded with status != 'archived'
                # (in-transaction re-check); on zero rowcount a concurrent
                # sweep (or migration-time archive) already owns the ARCHIVE
                # audit row — write none here.
                if archived:
                    await record_audit(
                        session,
                        action=AuditAction.ARCHIVE,
                        target_type=AuditTargetType.SERVICE_ACCOUNT,
                        target_id=row.id,
                        actor_type=_AUDIT_ACTOR_TYPE,
                        actor_id=_AUDIT_ACTOR_ID,
                        reason="theme8_sa_migration_sweep",
                        origin=Origin.SYSTEM.value,
                    )
            outcome.swept.append(row.id)
            outcome.access_tokens_revoked += access_revoked
            outcome.refresh_tokens_revoked += refresh_revoked
            logger.info(
                "service_account_migration_swept",
                service_account_id=row.id,
                successor_agent_id=(
                    None if row.migrated_to_actor_id == SKIPPED_STAMP else row.migrated_to_actor_id
                ),
                archived=archived,
                access_revoked_count=access_revoked,
                refresh_revoked_count=refresh_revoked,
            )

        # Control DB (H2): the sva_-keyed inline rules, after the admin pass.
        # Nothing is copied here: the successor's rules were copied before its
        # stamp committed, and re-copying onto an existing successor binding
        # could refill a list emptied on purpose (the retirement reports any
        # empty successor binding as a WARNING before the sweep runs).
        if self._ctx.has_db("control") and stamps:
            async with self._ctx.control_db.transaction() as control_session:
                holders = await ServiceAccountMigrationRepository.list_service_account_rule_holders(
                    control_session
                )
                for sa_id in sorted(holders & stamps.keys()):
                    outcome.permission_rules_deleted += (
                        await AgentPermissionRuleRepository.delete_for_agent(control_session, sa_id)
                    )

        logger.info(
            "service_account_migration_sweep_run",
            swept=len(outcome.swept),
            access_revoked_count=outcome.access_tokens_revoked,
            refresh_revoked_count=outcome.refresh_tokens_revoked,
            permission_rules_deleted=outcome.permission_rules_deleted,
        )
        return outcome

    # ------------------------------------------------------------- retirement

    async def retire(self) -> RetirementOutcome:
        """Migrate, verify, then sweep every service account — the pre-drop step.

        Needs the admin and control DBs. Order is load-bearing: nothing is
        swept or deleted until the verification passed, so a refusal leaves
        every SA-side original in place and a re-run (after the fix) is safe.

        1. :meth:`run` — migrate every unstamped SA (earlier ones are left
           alone).
        2. Copy onto earlier successors any SA grant or binding created after
           the stamp, with the binding's inline rules — only where the
           successor has no counterpart and the audit trail shows it never
           lost one (removed scope, purged binding). A successor that is
           archived or gone gets nothing. Everything withheld becomes a
           WARNING line; nothing an operator removed is resurrected.
        3. Verify: no failed row, nothing unstamped, grant and binding twins
           present (withheld rows excepted), exact inline-rule parity for this
           run's migrations. Any problem raises
           :class:`ServiceAccountRetirementError`. An earlier successor binding
           with no inline rules is a WARNING, never re-copied. (There is no
           API-key digest parity check: ``sak_`` keys stop working in 0.41
           whatever the successor's credential row holds.)
        4. :meth:`sweep`, then delete the ``sva_`` rules no SA row owns.
        5. WARN with every service account → successor agent id
           (:data:`SAK_KEYS_RETIRED_WARNING`) and every withheld copy.

        Raises:
            ServiceAccountRetirementError: the verification failed.
        """
        if not await self.tables_present():
            return RetirementOutcome(action="no_tables")
        async with hold_run_lock(self._ctx, SA_RETIREMENT_LOCK_KEY):
            outcomes = await self.run()
            migrated_now = {o.service_account_id for o in outcomes if o.outcome in _MIGRATED_NOW}
            post_stamp = await self._copy_post_stamp_rows(migrated_now)
            problems, rule_warnings = await self._verification_problems(
                outcomes, migrated_now, withheld=post_stamp.withheld_keys
            )
            if problems:
                error = ServiceAccountRetirementError(problems)
                logger.error(
                    "service_account_retirement_refused",
                    service_account_ids=list(error.service_account_ids),
                    problem_count=len(error.problems),
                    actionable_step=_RETIREMENT_RERUN_STEP,
                )
                raise error
            async with self._ctx.admin_db.session() as session:
                stamps = await ServiceAccountMigrationRepository.list_stamped(session)
            swept = await self.sweep()
            orphans = 0
            async with self._ctx.control_db.transaction() as control_session:
                orphans = await ServiceAccountMigrationRepository.delete_service_account_rules(
                    control_session
                )
        result = RetirementOutcome(
            action="retired",
            migrated=len(migrated_now),
            skipped=sum(1 for o in outcomes if o.outcome == "skipped-non-active"),
            already_migrated=sum(1 for o in outcomes if o.outcome == "already_migrated"),
            post_stamp_grants_copied=post_stamp.grants,
            post_stamp_bindings_copied=post_stamp.bindings,
            post_stamp_permission_rules_copied=post_stamp.rules,
            swept=len(swept.swept),
            access_tokens_revoked=swept.access_tokens_revoked,
            refresh_tokens_revoked=swept.refresh_tokens_revoked,
            permission_rules_deleted=swept.permission_rules_deleted,
            orphan_permission_rules_deleted=orphans,
            successors={
                sa_id: None if stamp == SKIPPED_STAMP else stamp
                for sa_id, stamp in sorted(stamps.items())
            },
            warnings=[w.line() for w in (*post_stamp.withheld, *rule_warnings)],
        )
        logger.info("service_account_retirement_done", **_log_fields(asdict(result)))
        for warning in (*post_stamp.withheld, *rule_warnings):
            logger.warning(
                "service_account_retirement_not_copied",
                service_account_id=warning.service_account_id,
                successor_agent_id=warning.successor_agent_id,
                not_copied=warning.not_copied,
                why=warning.reason,
                actionable_step="Re-grant it on the successor agent if it still needs it.",
            )
        if result.successors:
            logger.warning(
                "service_account_keys_retired",
                service_account_count=len(result.successors),
                successors=result.successors,
                actionable_step=SAK_KEYS_RETIRED_WARNING,
            )
        return result

    async def _copy_post_stamp_rows(self, migrated_now: set[str]) -> _PostStampCopy:
        """Twin onto earlier successors the SA grants/bindings created post-stamp.

        A post-stamp SA row never had a twin, but the successor may have held
        the same scope or binding on its own and lost it since; the audit trail
        records both removal paths (see
        :meth:`ServiceAccountMigrationRepository.list_successor_removals`), and
        such a row is withheld, as is anything for an archived or missing
        successor. The binding twins' inline rules are copied first, in the
        control DB (inert until the admin transaction below creates the
        binding), then one admin transaction writes the twins and one GRANT
        audit row per successor that gained anything.
        """
        result = _PostStampCopy()
        async with self._ctx.admin_db.session() as session:
            grant_gaps = [
                g
                for g in await ServiceAccountMigrationRepository.list_grant_twin_gaps(session)
                if g.post_stamp and g.service_account_id not in migrated_now
            ]
            binding_gaps = [
                b
                for b in await ServiceAccountMigrationRepository.list_binding_twin_gaps(session)
                if b.post_stamp and b.service_account_id not in migrated_now
            ]
            successor_ids = {g.successor_agent_id for g in (*grant_gaps, *binding_gaps)}
            statuses = await ServiceAccountMigrationRepository.list_agent_statuses(
                session, successor_ids
            )
            (
                removed_scopes,
                purged_bindings,
            ) = await ServiceAccountMigrationRepository.list_successor_removals(
                session, successor_ids
            )

        def _unusable(agent_id: str) -> str | None:
            status = statuses.get(agent_id)
            if status is None:
                return "the successor agent no longer exists"
            if status == ActorStatus.ARCHIVED:
                return "the successor agent is archived"
            return None

        grants_to_copy = []
        for g in grant_gaps:
            why = _unusable(g.successor_agent_id)
            if why is None and (g.successor_agent_id, g.scope) in removed_scopes:
                why = "the audit log shows this scope was removed from the successor"
            if why is None:
                grants_to_copy.append(g)
            else:
                result.withhold(
                    RetirementWarning(
                        g.service_account_id, g.successor_agent_id, f"scope grant {g.scope!r}", why
                    ),
                    ("grant", g.service_account_id, str(g.scope)),
                )
        bindings_to_copy = []
        for b in binding_gaps:
            why = _unusable(b.successor_agent_id)
            if why is None and (b.successor_agent_id, b.credential_id) in purged_bindings:
                why = "the audit log shows this binding was purged from the successor"
            if why is None:
                bindings_to_copy.append(b)
            else:
                result.withhold(
                    RetirementWarning(
                        b.service_account_id,
                        b.successor_agent_id,
                        f"credential binding {b.credential_id} (and its inline rules)",
                        why,
                    ),
                    ("binding", b.service_account_id, str(b.credential_id)),
                )

        if bindings_to_copy:
            by_pair: dict[tuple[str, str], list[str]] = {}
            for b in bindings_to_copy:
                by_pair.setdefault((b.service_account_id, b.successor_agent_id), []).append(
                    str(b.credential_id)
                )
            async with self._ctx.control_db.transaction() as control_session:
                for (sa_id, agent_id), credential_ids in sorted(by_pair.items()):
                    result.rules += await ServiceAccountMigrationRepository.copy_permission_rules(
                        control_session,
                        service_account_id=sa_id,
                        agent_id=agent_id,
                        credential_ids=credential_ids,
                    )

        async with self._ctx.admin_db.transaction() as session:
            copied: dict[tuple[str, str], dict[str, list[str]]] = {}
            for gap in grants_to_copy:
                if await ServiceAccountMigrationRepository.copy_grant_twin(
                    session, agent_id=gap.successor_agent_id, scope=gap.scope
                ):
                    result.grants += 1
                    key = (gap.service_account_id, gap.successor_agent_id)
                    copied.setdefault(key, {"scopes": [], "bindings": []})["scopes"].append(
                        str(gap.scope)
                    )
            for gap in bindings_to_copy:
                if await ServiceAccountMigrationRepository.copy_binding_twin(
                    session, agent_id=gap.successor_agent_id, source=gap
                ):
                    result.bindings += 1
                    key = (gap.service_account_id, gap.successor_agent_id)
                    copied.setdefault(key, {"scopes": [], "bindings": []})["bindings"].append(
                        str(gap.credential_id)
                    )
            for (sa_id, agent_id), rows in sorted(copied.items()):
                await record_audit(
                    session,
                    action=AuditAction.GRANT,
                    target_type=AuditTargetType.AGENT,
                    target_id=agent_id,
                    actor_type=_AUDIT_ACTOR_TYPE,
                    actor_id=_AUDIT_ACTOR_ID,
                    after={
                        "service_account_id": sa_id,
                        "copied_scopes": rows["scopes"],
                        "copied_credential_ids": rows["bindings"],
                    },
                    reason="theme8_sa_retirement_post_stamp_copy",
                    origin=Origin.SYSTEM.value,
                )
                logger.info(
                    "service_account_retirement_post_stamp_copy",
                    service_account_id=sa_id,
                    successor_agent_id=agent_id,
                    copied_scopes=rows["scopes"],
                    copied_binding_count=len(rows["bindings"]),
                )
        return result

    async def _verification_problems(
        self,
        outcomes: list[ServiceAccountMigrationOutcome],
        migrated_now: set[str],
        *,
        withheld: frozenset[tuple[str, str, str]] = frozenset(),
    ) -> tuple[list[RetirementProblem], list[RetirementWarning]]:
        """The pre-sweep acceptance checks, plus the non-blocking rule warnings.

        No problems means the drop may proceed. ``withheld`` holds the
        ``(kind, sa_id, scope|credential_id)`` post-stamp rows deliberately not
        copied (already reported as warnings), so they are not problems.
        """
        problems = [
            RetirementProblem(o.service_account_id, f"migration failed ({o.reason})")
            for o in outcomes
            if o.outcome == "failed"
        ]
        failed = {p.service_account_id for p in problems}
        async with self._ctx.admin_db.session() as session:
            unstamped = await ServiceAccountMigrationRepository.list_unstamped_ids(session)
            grant_gaps = await ServiceAccountMigrationRepository.list_grant_twin_gaps(session)
            binding_gaps = await ServiceAccountMigrationRepository.list_binding_twin_gaps(session)
            pairs = await ServiceAccountMigrationRepository.list_migrated_pairs(session)
        problems += [
            RetirementProblem(sa_id, "not migrated") for sa_id in unstamped if sa_id not in failed
        ]
        # For an SA stamped by an earlier run only post-stamp rows count: the
        # others were copied at stamp time and may since have been removed
        # from the successor on purpose.
        problems += [
            RetirementProblem(
                g.service_account_id,
                f"successor {g.successor_agent_id} lacks the scope grant {g.scope!r}",
            )
            for g in grant_gaps
            if (g.service_account_id in migrated_now or g.post_stamp)
            and ("grant", g.service_account_id, str(g.scope)) not in withheld
        ]
        problems += [
            RetirementProblem(
                b.service_account_id,
                f"successor {b.successor_agent_id} lacks the binding for credential "
                f"{b.credential_id}",
            )
            for b in binding_gaps
            if (b.service_account_id in migrated_now or b.post_stamp)
            and ("binding", b.service_account_id, str(b.credential_id)) not in withheld
        ]
        warnings: list[RetirementWarning] = []
        if pairs:
            actor_ids = [sa_id for sa_id, _ in pairs] + [agent_id for _, agent_id in pairs]
            async with self._ctx.control_db.session() as control_session:
                counts = await ServiceAccountMigrationRepository.count_permission_rules_by_binding(
                    control_session, actor_ids
                )
            successor_of = dict(pairs)
            problems += rule_parity_problems(counts, successor_of, migrated_now)
            # A withheld binding is already reported, rules included.
            skip = {(sa, cred) for kind, sa, cred in withheld if kind == "binding"}
            warnings = rule_parity_warnings(counts, successor_of, migrated_now, skip=skip)
        return problems, warnings


__all__ = [
    "SAK_KEYS_RETIRED_WARNING",
    "SYSTEM_ACTOR",
    "RetirementOutcome",
    "RetirementProblem",
    "RetirementWarning",
    "ServiceAccountMigrationOutcome",
    "ServiceAccountMigrationService",
    "ServiceAccountRetirementError",
    "SweepOutcome",
    "rule_parity_problems",
    "rule_parity_warnings",
]
