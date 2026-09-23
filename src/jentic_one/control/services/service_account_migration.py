"""Theme-8 Phase 1 — the service-account → agent migration job.

Converts every service account into a successor **agent** (copy-then-sweep,
N1): the SA's stored scope grants, toolkit/credential bindings, and API-key
digest are COPIED onto a raw-SQL-minted successor agent, the SA's opaque
sessions are revoked (H-1), and the row is stamped
(``migrated_to_actor_id`` + ``migrated_at``) — all in one admin transaction
per SA, with per-SA audit rows under the system actor (F4). The SA-side
originals stay live until the deferred :meth:`sweep` so old-image pods keep
resolving migrated keys through a rolling upgrade (H-B). The control-DB
half — the ``toolkit_keys`` re-stamp and the copy of the ``sva_``-keyed
per-binding inline permission rules (H2) — runs in its own transaction after
the admin commit, is idempotent, and is retried on every ``already_migrated``
re-run; a control-DB failure is reported as a ``failed`` row (L1).

Disposition (OQ-1, rev 5): ``active`` → full migration (successor
``active``); ``disabled`` → full migration (successor ``disabled``, NF-2);
``pending``/``rejected``/``archived`` → skip-but-stamp (no successor; stamp
value ``skipped``). Successor creation is raw SQL — never
``AgentService.create()``/``approve()`` (F1: both default-grant
``DEFAULT_AGENT_SCOPES``; a zero-grant SA must yield a zero-grant
successor).

Idempotency: the stamp short-circuits re-runs, so the boot job runs on every
start (like theme-5 key retirement) and catches SAs created during the
window (until Phase 2 removed ``POST /service-accounts``, F5). Concurrency: one
admin transaction per SA (``BEGIN IMMEDIATE`` on SQLite), a pg advisory-lock
fast path, an in-transaction stamp re-check, and — the real backstop — the
``uq_agent_credentials_api_key_hash`` unique partial index: a losing
double-mint fails its whole transaction and is reported, never a partial
write.

The deferred :meth:`sweep` (N3) deletes the SA-keyed grant/binding rows
(and, in the control DB, the ``sva_``-keyed inline permission rules), NULLs
the SA-side digest, revokes any SA sessions minted since the migration, and
archives the row — gated on a minimum stamp
age (``services.service_account_sweep_min_stamp_age_hours``) as the
full-fleet-rollout proxy; ``--sweep-migrated`` overrides. :meth:`verify`
runs the acceptance queries; ``--verify --acknowledge`` writes the
``service_account_migration_acks`` sentinel row the Phase-4 drops require
(the toolkit-flattening precedent), only when verification passed in the
same invocation.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Any

import structlog

from jentic_one import __version__
from jentic_one.control.repos.agent_permission_rule_repo import AgentPermissionRuleRepository
from jentic_one.control.repos.service_account_migration_repo import (
    SKIPPED_STAMP,
    SYSTEM_ACTOR,
    ServiceAccountMigrationRepository,
)
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
_AUDIT_ACTOR_ID = "migrate-service-accounts"


class _ConcurrentWinnerError(Exception):
    """A concurrent run stamped this SA first — roll back, report already_migrated."""


class _ServiceAccountVanishedError(Exception):
    """The SA row listed by ``run()`` no longer exists at migration time."""


@dataclass(frozen=True)
class ServiceAccountMigrationOutcome:
    """One JSONL report line: what happened to one service account."""

    service_account_id: str
    outcome: str  # migrated | migrated-disabled | skipped-non-active |
    #             # already_migrated | failed
    successor_agent_id: str | None = None
    stored_scope_count: int = 0
    toolkit_binding_count: int = 0
    credential_binding_count: int = 0
    #: Control-DB ``agent_permission_rules`` rows copied sva_ → agnt_ (H2).
    permission_rule_count: int = 0
    access_tokens_revoked: int = 0
    refresh_tokens_revoked: int = 0
    #: OQ-1 — operators get the client-credentials holder list before Phase 2
    #: kills the grant.
    had_client_secret: bool = False
    #: OQ-5 (report-only in Phase 1): the successor agent is visible to
    #: ``owner:agents:read`` holders via ``parent_actor_id=owner_id``, where
    #: the SA was governed by ``owner:service-accounts:read``. The note also
    #: names the other behaviour changes an operator must review per SA:
    #: (a) control-DB objects ``created_by`` the ``sva_`` id are NOT
    #: re-attributed, so the successor loses owner-scoped access to them;
    #: (b) with ``parent_actor_id=owner`` any copied ``owner:*`` delegation
    #: scope now widens to the owner's resources; (c) migrated ``sak_``
    #: callers now act as an agent — ``POST /oauth/mint`` is gone (404),
    #: ``/integrations:connect`` refuses
    #: ``agent_id`` in the body, and an agent-initiated connect session
    #: cannot be confirmed by the agent itself (``_forbid_self_confirm``).
    owner_visibility_note: str | None = None
    reason: str | None = None


@dataclass
class SweepOutcome:
    """Result of one sweep pass."""

    swept: list[str] = field(default_factory=list)
    skipped_young: int = 0
    #: Opaque SA sessions revoked by the sweep (M1) — client-credentials
    #: holders can mint fresh SA sessions until the row is archived.
    access_tokens_revoked: int = 0
    refresh_tokens_revoked: int = 0
    #: Control-DB ``sva_``-keyed inline rules deleted (H2).
    permission_rules_deleted: int = 0


@dataclass
class VerificationResult:
    """Result of the acceptance queries (W9)."""

    passed: bool
    unstamped_count: int
    grant_twin_missing_count: int
    unrevoked_token_count: int
    digest_mismatch_count: int
    post_stamp_mutation_count: int
    #: Criterion 6 (H2): ``sva_``-keyed inline-rule bindings whose successor
    #: twin holds a different rule count. Not persisted on the ack row (the
    #: sentinel is only written when every count is zero).
    inline_rule_mismatch_count: int = 0
    findings: list[dict[str, Any]] = field(default_factory=list)
    acknowledged: bool = False


class ServiceAccountMigrationService:
    """Orchestrates the migration, sweep, and verify passes."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def run(self, *, diff_only: bool = False) -> list[ServiceAccountMigrationOutcome]:
        """Migrate every service account; return one outcome per SA.

        ``diff_only`` evaluates dispositions and prints the report without
        writing anything (the flattening ``--diff-only`` precedent).
        """
        async with self._ctx.admin_db.session() as session:
            rows = await ServiceAccountMigrationRepository.list_service_accounts(session)

        outcomes: list[ServiceAccountMigrationOutcome] = []
        for row in rows:
            if diff_only:
                outcome = self._preview(row)
            else:
                outcome = await self._migrate_one(row)
            outcomes.append(outcome)
            logger.info("service_account_migration", diff_only=diff_only, **asdict(outcome))
        return outcomes

    @staticmethod
    def _disposition(status: str) -> tuple[str, str | None]:
        """OQ-1 switch: (outcome label, successor status or None for skip)."""
        if status == ActorStatus.ACTIVE:
            return "migrated", ActorStatus.ACTIVE.value
        if status == ActorStatus.DISABLED:
            return "migrated-disabled", ActorStatus.DISABLED.value
        return "skipped-non-active", None

    def _preview(self, row: Any) -> ServiceAccountMigrationOutcome:
        """Dry-run disposition for one SA row (no writes)."""
        if row.migrated_to_actor_id is not None:
            return ServiceAccountMigrationOutcome(
                service_account_id=row.id,
                outcome="already_migrated",
                successor_agent_id=(
                    None if row.migrated_to_actor_id == SKIPPED_STAMP else row.migrated_to_actor_id
                ),
                had_client_secret=row.client_secret_hash is not None,
            )
        label, _successor_status = self._disposition(row.status)
        return ServiceAccountMigrationOutcome(
            service_account_id=row.id,
            outcome=label,
            had_client_secret=row.client_secret_hash is not None,
            reason=None if label != "skipped-non-active" else f"status={row.status}",
            owner_visibility_note=(
                self._visibility_note(row) if label != "skipped-non-active" else None
            ),
        )

    @staticmethod
    def _visibility_note(row: Any) -> str:
        """Per-SA review note — see the field comment on ``owner_visibility_note``."""
        return (
            f"successor agent becomes visible to owner:agents:read via"
            f" parent_actor_id={row.owner_id} (OQ-5, report-only); any copied owner:*"
            f" scope now also reaches that owner's resources; control-DB objects"
            f" created_by {row.id} are not re-attributed (the successor loses"
            f" owner-scoped access to them); sak_ callers now act as an agent:"
            f" POST /oauth/mint is gone (404), /integrations:connect refuses agent_id"
            f" in the body, and agent-initiated connect sessions cannot be"
            f" self-confirmed"
        )

    async def _sync_control(self, service_account_id: str, agent_id: str) -> int:
        """Idempotent control-DB half of one SA migration; returns rules copied.

        One control transaction: re-stamp ``toolkit_keys.migrated_actor_id``
        (M-E) and copy the ``sva_``-keyed per-binding inline permission
        rules onto the successor (H2). Called after the admin commit on
        fresh migrations AND on every ``already_migrated`` re-run (M1): the
        two DBs cannot share a transaction, so a crash between them would
        otherwise lose this step forever behind the stamp short-circuit.
        """
        if not self._ctx.has_db("control"):
            return 0
        async with self._ctx.control_db.transaction() as control_session:
            await ServiceAccountMigrationRepository.restamp_toolkit_keys(
                control_session, service_account_id=service_account_id, agent_id=agent_id
            )
            return await ServiceAccountMigrationRepository.copy_permission_rules(
                control_session, service_account_id=service_account_id, agent_id=agent_id
            )

    async def _try_sync_control(
        self, service_account_id: str, agent_id: str
    ) -> tuple[int, str | None]:
        """L1: a control-DB failure is a row outcome, never a run abort.

        Returns ``(rules_copied, failure_reason)``. The admin side has
        already committed; the next run heals via the ``already_migrated``
        path, which re-runs this step.
        """
        try:
            return await self._sync_control(service_account_id, agent_id), None
        except Exception as exc:
            logger.warning(
                "service_account_migration_control_sync_failed",
                service_account_id=service_account_id,
                successor_agent_id=agent_id,
                error=str(exc),
                error_type=type(exc).__name__,
                actionable_step=(
                    "The admin-side migration committed; re-run "
                    "`jentic_one migrate-service-accounts` to retry the control-DB step."
                ),
            )
            return 0, f"control_sync_error:{type(exc).__name__}"

    async def _migrate_one(self, row: Any) -> ServiceAccountMigrationOutcome:
        """Copy → revoke → stamp → audit, one admin transaction; then the
        control-DB step (toolkit-key re-stamp + inline-rule copy; separate,
        idempotent, after the admin commit)."""
        if row.migrated_to_actor_id is not None:
            # M1: the control-DB step runs AFTER the admin commit, so a
            # crash between the two loses it — re-run it (idempotent) on the
            # already_migrated path instead of short-circuiting past it.
            rules_copied = 0
            sync_error: str | None = None
            if row.migrated_to_actor_id != SKIPPED_STAMP:
                rules_copied, sync_error = await self._try_sync_control(
                    row.id, row.migrated_to_actor_id
                )
            return ServiceAccountMigrationOutcome(
                service_account_id=row.id,
                outcome="failed" if sync_error is not None else "already_migrated",
                permission_rule_count=rules_copied,
                reason=sync_error,
                successor_agent_id=(
                    None if row.migrated_to_actor_id == SKIPPED_STAMP else row.migrated_to_actor_id
                ),
                had_client_secret=row.client_secret_hash is not None,
            )

        now = dt.datetime.now(dt.UTC)
        # H1: disposition, digest, owner, and name are derived from the row
        # re-read INSIDE the per-SA transaction (below), never from the
        # ``run()`` snapshot — a disable or key rotation landing between the
        # list and this transaction must be reflected in the successor.
        current: Any = row
        label = "failed"
        successor_status: str | None = None
        agent_id: str | None = None
        stored_scopes = toolkit_bindings = credential_bindings = 0
        access_revoked = refresh_revoked = 0

        try:
            async with self._ctx.admin_db.transaction() as session:
                await ServiceAccountMigrationRepository.acquire_migration_lock(session, row.id)
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
                    )
                    stored_scopes = await ServiceAccountMigrationRepository.copy_scope_grants(
                        session, service_account_id=row.id, agent_id=agent_id
                    )
                    (
                        toolkit_bindings,
                        credential_bindings,
                    ) = await ServiceAccountMigrationRepository.copy_bindings(
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
                        after={"copied_scope_count": stored_scopes},
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

        # Control DB (separate, idempotent, after the admin commit): re-stamp
        # toolkit_keys.migrated_actor_id so key revocation keeps disabling the
        # right actor (M-E; the W8 retarget keeps set_actor_status meaningful)
        # and copy the per-binding inline rules (H2). A failure here is a row
        # outcome (L1); a crash between the admin commit and this call is
        # healed by the M1 re-run on the already_migrated path.
        rules_copied = 0
        sync_error = None
        if agent_id is not None:
            rules_copied, sync_error = await self._try_sync_control(current.id, agent_id)

        reason: str | None = None
        if sync_error is not None:
            reason = sync_error
        elif label == "skipped-non-active":
            reason = f"status={current.status}"
        return ServiceAccountMigrationOutcome(
            service_account_id=current.id,
            outcome="failed" if sync_error is not None else label,
            successor_agent_id=agent_id,
            stored_scope_count=stored_scopes,
            toolkit_binding_count=toolkit_bindings,
            credential_binding_count=credential_bindings,
            permission_rule_count=rules_copied,
            access_tokens_revoked=access_revoked,
            refresh_tokens_revoked=refresh_revoked,
            had_client_secret=current.client_secret_hash is not None,
            owner_visibility_note=(
                self._visibility_note(current) if agent_id is not None else None
            ),
            reason=reason,
        )

    # ------------------------------------------------------------------ sweep

    async def sweep(self, *, ignore_age_gate: bool = False) -> SweepOutcome:
        """W3 — delete SA-keyed originals for stamped rows, archive the SA.

        The automatic arm (boot) honours the N3 age gate
        (``services.service_account_sweep_min_stamp_age_hours``; ``0``
        disables the gate, a negative value disables the automatic arm
        entirely — the caller checks that). ``ignore_age_gate`` is the
        ``--sweep-migrated`` operator override for uniform fleets.

        Per SA, one admin transaction deletes the SA-keyed originals,
        revokes every outstanding opaque SA session (M1 — client-credentials
        holders can keep minting SA sessions until the row is archived, and
        verify criterion 3 would otherwise wait out the refresh TTL), and
        archives the row. A control-DB pass then deletes the ``sva_``-keyed
        inline permission rules (H2) for every stamped SA passing the same
        gate — including rows whose admin side a previous, interrupted sweep
        already finished.

        Note (E4): ``skipped_young`` comes from a second, non-atomic
        ``list_sweepable`` query — a row stamped between the two queries can
        skew the count by one. Accepted: the count is informational only.
        """
        age_hours = self._ctx.config.services.service_account_sweep_min_stamp_age_hours
        stamped_before: dt.datetime | None = None
        if not ignore_age_gate and age_hours > 0:
            stamped_before = dt.datetime.now(dt.UTC) - dt.timedelta(hours=age_hours)

        async with self._ctx.admin_db.session() as session:
            rows = await ServiceAccountMigrationRepository.list_sweepable(
                session, stamped_before=stamped_before
            )
            all_stamped = await ServiceAccountMigrationRepository.list_sweepable(
                session, stamped_before=None
            )
            # Snapshotted up-front with ``rows`` so the control pass never
            # reaches a row stamped after the admin pass was planned.
            gated_stamps = await ServiceAccountMigrationRepository.list_stamped(
                session, stamped_before=stamped_before
            )

        outcome = SweepOutcome(skipped_young=len(all_stamped) - len(rows))
        for row in rows:
            async with self._ctx.admin_db.transaction() as session:
                archived = await ServiceAccountMigrationRepository.sweep_service_account(
                    session, service_account_id=row.id
                )
                # M1: the sweep is the kill lever for client-credentials
                # holders during the window — revoke whatever SA sessions
                # they minted since the migration-time revoke, in the same
                # transaction that archives the row (after which the grant
                # refuses them: non-active SAs cannot authenticate).
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
                access_tokens_revoked=access_revoked,
                refresh_tokens_revoked=refresh_revoked,
            )

        # Control DB (H2): the sva_-keyed inline rules. Runs after the admin
        # pass so old-image pods keep the SA arm's rules until the SA row is
        # archived; idempotent (a re-run only finds rows a crash left). The
        # successor twin is ensured first (binding-level idempotent copy), so
        # a migration whose control step failed (L1) and was never healed
        # does not lose its rules to the sweep.
        if self._ctx.has_db("control") and gated_stamps:
            async with self._ctx.control_db.transaction() as control_session:
                holders = await ServiceAccountMigrationRepository.list_service_account_rule_holders(
                    control_session
                )
                for sa_id in sorted(holders & gated_stamps.keys()):
                    successor = gated_stamps[sa_id]
                    if successor != SKIPPED_STAMP:
                        await ServiceAccountMigrationRepository.copy_permission_rules(
                            control_session, service_account_id=sa_id, agent_id=successor
                        )
                    outcome.permission_rules_deleted += (
                        await AgentPermissionRuleRepository.delete_for_agent(control_session, sa_id)
                    )

        logger.info(
            "service_account_migration_sweep_run",
            swept=len(outcome.swept),
            skipped_young=outcome.skipped_young,
            access_tokens_revoked=outcome.access_tokens_revoked,
            refresh_tokens_revoked=outcome.refresh_tokens_revoked,
            permission_rules_deleted=outcome.permission_rules_deleted,
            ignore_age_gate=ignore_age_gate,
        )
        return outcome

    # ----------------------------------------------------------------- verify

    async def verify(self, *, acknowledge: bool = False) -> VerificationResult:
        """W9 — the acceptance queries; optionally write the Phase-4 gate row.

        The sentinel is written only when ``acknowledge`` is set AND this
        verification passed — never from any other code path (the
        ``toolkit_flattening_acks`` precedent). It is necessary but not
        sufficient: the Phase-4 drop migration re-verifies at drop time
        (F5 x M-C).

        Criterion 6 (H2) is cross-DB: for every fully-migrated SA, each
        ``(sva_, credential)`` binding still holding inline rules in the
        control DB must have a successor twin with the same rule count.
        After the sweep the ``sva_`` rows are gone, so swept rows pass.

        Note (E4): the counts run in one session per DB but WITHOUT a snapshot
        transaction — writes landing between the queries can make the set
        internally inconsistent. Accepted: Phase 4 re-verifies at drop time,
        and the sentinel is only advisory until then.
        """
        now = dt.datetime.now(dt.UTC)
        async with self._ctx.admin_db.session() as session:
            unstamped = await ServiceAccountMigrationRepository.count_unstamped(session)
            twin_missing = await ServiceAccountMigrationRepository.count_grant_twin_missing(session)
            unrevoked = await ServiceAccountMigrationRepository.count_unrevoked_tokens(
                session, now=now
            )
            digest_mismatch = await ServiceAccountMigrationRepository.count_digest_mismatches(
                session
            )
            post_stamp = await ServiceAccountMigrationRepository.count_post_stamp_mutations(session)
            migrated_pairs = await ServiceAccountMigrationRepository.list_migrated_pairs(session)
        rule_mismatch = await self._count_inline_rule_mismatches(migrated_pairs)

        result = VerificationResult(
            passed=(
                unstamped == 0
                and twin_missing == 0
                and unrevoked == 0
                and digest_mismatch == 0
                and post_stamp == 0
                and rule_mismatch == 0
            ),
            unstamped_count=unstamped,
            grant_twin_missing_count=twin_missing,
            unrevoked_token_count=unrevoked,
            digest_mismatch_count=digest_mismatch,
            post_stamp_mutation_count=post_stamp,
            inline_rule_mismatch_count=rule_mismatch,
        )
        result.findings.append(
            {
                "category": "verify_summary",
                "passed": result.passed,
                "unstamped_count": unstamped,
                "grant_twin_missing_count": twin_missing,
                "unrevoked_token_count": unrevoked,
                "digest_mismatch_count": digest_mismatch,
                "post_stamp_mutation_count": post_stamp,
                "inline_rule_mismatch_count": rule_mismatch,
                "tool_version": __version__,
            }
        )
        logger.info(
            "service_account_migration_verify",
            passed=result.passed,
            unstamped_count=unstamped,
            grant_twin_missing_count=twin_missing,
            unrevoked_token_count=unrevoked,
            digest_mismatch_count=digest_mismatch,
            post_stamp_mutation_count=post_stamp,
            inline_rule_mismatch_count=rule_mismatch,
        )

        if acknowledge and result.passed:
            async with self._ctx.admin_db.transaction() as session:
                ack_id = await ServiceAccountMigrationRepository.record_acknowledgement(
                    session,
                    acknowledged_at=now,
                    unstamped_count=unstamped,
                    grant_twin_missing_count=twin_missing,
                    unrevoked_token_count=unrevoked,
                    digest_mismatch_count=digest_mismatch,
                    post_stamp_mutation_count=post_stamp,
                    report_finding_count=len(result.findings),
                    tool_version=__version__,
                )
            result.acknowledged = True
            logger.info("service_account_migration_acknowledged", ack_id=ack_id)
        elif acknowledge:
            logger.warning(
                "service_account_migration_acknowledge_refused",
                detail="verification failed; sentinel not written",
            )
        return result

    async def _count_inline_rule_mismatches(self, migrated_pairs: list[tuple[str, str]]) -> int:
        """Criterion 6: sva_ bindings with rules whose successor twin count differs."""
        if not migrated_pairs or not self._ctx.has_db("control"):
            return 0
        actor_ids = [sa_id for sa_id, _ in migrated_pairs] + [a for _, a in migrated_pairs]
        async with self._ctx.control_db.session() as control_session:
            counts = await ServiceAccountMigrationRepository.count_permission_rules_by_binding(
                control_session, actor_ids
            )
        successor_of = dict(migrated_pairs)
        mismatches = 0
        for (actor_id, credential_id), n in counts.items():
            successor = successor_of.get(actor_id)
            if successor is None:
                continue  # a successor-side row, or a non-migrated actor
            if counts.get((successor, credential_id), 0) != n:
                mismatches += 1
        return mismatches


__all__ = [
    "SYSTEM_ACTOR",
    "ServiceAccountMigrationOutcome",
    "ServiceAccountMigrationService",
    "SweepOutcome",
    "VerificationResult",
]
