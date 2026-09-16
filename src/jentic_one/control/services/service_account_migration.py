"""Theme-8 Phase 1 — the service-account → agent migration job.

Converts every service account into a successor **agent** (copy-then-sweep,
N1): the SA's stored scope grants, toolkit/credential bindings, and API-key
digest are COPIED onto a raw-SQL-minted successor agent, the SA's opaque
sessions are revoked (H-1), and the row is stamped
(``migrated_to_actor_id`` + ``migrated_at``) — all in one admin transaction
per SA, with per-SA audit rows under the system actor (F4). The SA-side
originals stay live until the deferred :meth:`sweep` so old-image pods keep
resolving migrated keys through a rolling upgrade (H-B).

Disposition (OQ-1, rev 5): ``active`` → full migration (successor
``active``); ``disabled`` → full migration (successor ``disabled``, NF-2);
``pending``/``rejected``/``archived`` → skip-but-stamp (no successor; stamp
value ``skipped``). Successor creation is raw SQL — never
``AgentService.create()``/``approve()`` (F1: both default-grant
``DEFAULT_AGENT_SCOPES``; a zero-grant SA must yield a zero-grant
successor).

Idempotency: the stamp short-circuits re-runs, so the boot job runs on every
start (like theme-5 key retirement) and catches SAs created during the
window (``POST /service-accounts`` stays unguarded, F5). Concurrency: one
admin transaction per SA (``BEGIN IMMEDIATE`` on SQLite), a pg advisory-lock
fast path, an in-transaction stamp re-check, and — the real backstop — the
``uq_agent_credentials_api_key_hash`` unique partial index: a losing
double-mint fails its whole transaction and is reported, never a partial
write.

The deferred :meth:`sweep` (N3) deletes the SA-keyed grant/binding rows,
NULLs the SA-side digest, and archives the row — gated on a minimum stamp
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
    access_tokens_revoked: int = 0
    refresh_tokens_revoked: int = 0
    #: OQ-1 — operators get the client-credentials holder list before Phase 2
    #: kills the grant.
    had_client_secret: bool = False
    #: OQ-5 (report-only in Phase 1): the successor agent is visible to
    #: ``owner:agents:read`` holders via ``parent_actor_id=owner_id``, where
    #: the SA was governed by ``owner:service-accounts:read``.
    owner_visibility_note: str | None = None
    reason: str | None = None


@dataclass
class SweepOutcome:
    """Result of one sweep pass."""

    swept: list[str] = field(default_factory=list)
    skipped_young: int = 0


@dataclass
class VerificationResult:
    """Result of the acceptance queries (W9)."""

    passed: bool
    unstamped_count: int
    grant_twin_missing_count: int
    unrevoked_token_count: int
    digest_mismatch_count: int
    post_stamp_mutation_count: int
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
        return (
            f"successor agent becomes visible to owner:agents:read via"
            f" parent_actor_id={row.owner_id} (OQ-5, report-only)"
        )

    async def _restamp_control(self, service_account_id: str, agent_id: str) -> None:
        """Idempotent control-DB re-stamp of ``toolkit_keys.migrated_actor_id``.

        Called after the admin commit on fresh migrations AND on every
        ``already_migrated`` re-run (M1): the two DBs cannot share a
        transaction, so a crash between them would otherwise lose the
        re-stamp forever behind the stamp short-circuit.
        """
        if not self._ctx.has_db("control"):
            return
        async with self._ctx.control_db.transaction() as control_session:
            await ServiceAccountMigrationRepository.restamp_toolkit_keys(
                control_session, service_account_id=service_account_id, agent_id=agent_id
            )

    async def _migrate_one(self, row: Any) -> ServiceAccountMigrationOutcome:
        """Copy → revoke → stamp → audit, one admin transaction; then the
        control-DB re-stamp (separate, idempotent, after the admin commit)."""
        if row.migrated_to_actor_id is not None:
            # M1: the control-DB re-stamp runs AFTER the admin commit, so a
            # crash between the two loses it — re-run it (idempotent) on the
            # already_migrated path instead of short-circuiting past it.
            if row.migrated_to_actor_id != SKIPPED_STAMP:
                await self._restamp_control(row.id, row.migrated_to_actor_id)
            return ServiceAccountMigrationOutcome(
                service_account_id=row.id,
                outcome="already_migrated",
                successor_agent_id=(
                    None if row.migrated_to_actor_id == SKIPPED_STAMP else row.migrated_to_actor_id
                ),
                had_client_secret=row.client_secret_hash is not None,
            )

        label, successor_status = self._disposition(row.status)
        now = dt.datetime.now(dt.UTC)
        agent_id: str | None = None
        stored_scopes = toolkit_bindings = credential_bindings = 0
        access_revoked = refresh_revoked = 0

        try:
            async with self._ctx.admin_db.transaction() as session:
                await ServiceAccountMigrationRepository.acquire_migration_lock(session, row.id)
                # In-transaction stamp re-check (FOR UPDATE on pg): a
                # concurrent winner is a clean no-op, never a double mint.
                stamp = await ServiceAccountMigrationRepository.stamp_of(
                    session, row.id, for_update=True
                )
                if stamp is not None:
                    raise _ConcurrentWinnerError(stamp)

                if successor_status is not None:
                    agent_id = await ServiceAccountMigrationRepository.create_successor_agent(
                        session,
                        service_account_id=row.id,
                        sa_name=row.name,
                        owner_id=row.owner_id,
                        status=successor_status,
                        api_key_hash=row.api_key_hash,
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
        # right actor (M-E); the W8 retarget keeps set_actor_status meaningful.
        # A crash between the admin commit and this call is healed by the M1
        # re-run on the already_migrated path.
        if agent_id is not None:
            await self._restamp_control(row.id, agent_id)

        return ServiceAccountMigrationOutcome(
            service_account_id=row.id,
            outcome=label,
            successor_agent_id=agent_id,
            stored_scope_count=stored_scopes,
            toolkit_binding_count=toolkit_bindings,
            credential_binding_count=credential_bindings,
            access_tokens_revoked=access_revoked,
            refresh_tokens_revoked=refresh_revoked,
            had_client_secret=row.client_secret_hash is not None,
            owner_visibility_note=(self._visibility_note(row) if agent_id is not None else None),
            reason=None if label != "skipped-non-active" else f"status={row.status}",
        )

    # ------------------------------------------------------------------ sweep

    async def sweep(self, *, ignore_age_gate: bool = False) -> SweepOutcome:
        """W3 — delete SA-keyed originals for stamped rows, archive the SA.

        The automatic arm (boot) honours the N3 age gate
        (``services.service_account_sweep_min_stamp_age_hours``; ``0``
        disables the gate, a negative value disables the automatic arm
        entirely — the caller checks that). ``ignore_age_gate`` is the
        ``--sweep-migrated`` operator override for uniform fleets.

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

        outcome = SweepOutcome(skipped_young=len(all_stamped) - len(rows))
        for row in rows:
            async with self._ctx.admin_db.transaction() as session:
                archived = await ServiceAccountMigrationRepository.sweep_service_account(
                    session, service_account_id=row.id
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
            logger.info(
                "service_account_migration_swept",
                service_account_id=row.id,
                successor_agent_id=(
                    None if row.migrated_to_actor_id == SKIPPED_STAMP else row.migrated_to_actor_id
                ),
            )
        logger.info(
            "service_account_migration_sweep_run",
            swept=len(outcome.swept),
            skipped_young=outcome.skipped_young,
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

        Note (E4): the five counts run in one session but WITHOUT a snapshot
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

        result = VerificationResult(
            passed=(
                unstamped == 0
                and twin_missing == 0
                and unrevoked == 0
                and digest_mismatch == 0
                and post_stamp == 0
            ),
            unstamped_count=unstamped,
            grant_twin_missing_count=twin_missing,
            unrevoked_token_count=unrevoked,
            digest_mismatch_count=digest_mismatch,
            post_stamp_mutation_count=post_stamp,
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


__all__ = [
    "SYSTEM_ACTOR",
    "ServiceAccountMigrationOutcome",
    "ServiceAccountMigrationService",
    "SweepOutcome",
    "VerificationResult",
]
