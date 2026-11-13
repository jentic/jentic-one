"""Cross-database repair service for vendor-doubled (#1079) and path-derived (#1388) identities.

Coordinates ``registry_db``, ``control_db``, and ``admin_db`` so existing
malformed rows are analysed with full cross-surface reference accounting
(revisions, operations, overlays, notes, agent bindings, agent auth keys,
permission rules, connect sessions, held executions, and append-only execution
history) before any rewrite runs:

- Open ``api``-target sessions without a credential repair are grouped by source
  identity and committed before their dependent API rename. Registry ``apis``
  rows are updated in place (preserving ``apis.id`` so revisions, operations,
  URL indexes, overlays, and notes stay attached).
- Control ``credentials`` and their attached open sessions are updated in place
  (preserving ``credentials.id`` so encrypted secret rows,
  ``agent_credential_bindings``, ``agent_permission_rules``, and historical
  ``execution_records`` references stay intact). Cross-database failures are
  compensated where possible and recorded in the applied backup otherwise.
- Historical ``execution_records`` and terminal ``held_executions`` /
  ``connect_sessions`` remain immutable.
- Colliding or ambiguous rows fail closed without mutating data and surface
  structured operator remediation.
- Every run produces a self-describing, secret-free backup document that
  :meth:`CredentialIdentityRepairService.restore_backup` rolls back idempotently.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import structlog

from jentic_one import __version__
from jentic_one.control.repos.credential_identity_repair_repo import (
    CredentialIdentityRepairRepository,
)
from jentic_one.control.services.run_lock import IDENTITY_REPAIR_LOCK_KEY, hold_run_lock
from jentic_one.shared.audit import record_audit_best_effort
from jentic_one.shared.context import Context
from jentic_one.shared.models.audit import AuditAction, AuditTargetType
from jentic_one.shared.models.credential_identity_repair import (
    OPEN_CONNECT_SESSION_STATES,
    IdentityRepairBackupError,
    PlannedApiRepair,
    PlannedConnectSessionRepair,
    PlannedCredentialRepair,
    RepairFinding,
    RepairPlan,
    RepairRunResult,
    RestoreOutcome,
    build_backup_document,
    build_repair_plan,
    validate_backup_document,
)

logger = structlog.get_logger(__name__)

_AUDIT_ACTOR_TYPE = "system:job"
_AUDIT_ACTOR_ID = "system:credential-identity-repair"
_AUDIT_REASON_REPAIR = "credential_identity_repair"
_AUDIT_REASON_ROLLBACK = "credential_identity_repair_rollback"
_AUDIT_REASON_COMPENSATION = "credential_identity_repair_compensation"


class CredentialIdentityRepairService:
    """Analyses, repairs, backs up, and restores malformed API/credential identities."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def run(
        self,
        *,
        dry_run: bool = False,
        before_apply: Callable[[RepairRunResult], Awaitable[None]] | None = None,
    ) -> RepairRunResult:
        """Inventory and repair under one lock, optionally persisting the live plan first."""
        async with hold_run_lock(self._ctx, IDENTITY_REPAIR_LOCK_KEY):
            return await self._run_locked(dry_run=dry_run, before_apply=before_apply)

    async def _run_locked(
        self,
        *,
        dry_run: bool,
        before_apply: Callable[[RepairRunResult], Awaitable[None]] | None,
    ) -> RepairRunResult:
        async with self._ctx.registry_db.session() as reg_session:
            apis = await CredentialIdentityRepairRepository.list_registry_apis(reg_session)
            manifest_entries = (
                await CredentialIdentityRepairRepository.list_catalog_manifest_entries(reg_session)
            )
            rev_urls = await CredentialIdentityRepairRepository.list_revision_urls_by_api(
                reg_session
            )

        async with self._ctx.control_db.session() as ctrl_session:
            credentials = await CredentialIdentityRepairRepository.list_credentials(ctrl_session)

        async with (
            self._ctx.registry_db.session() as reg_session,
            self._ctx.control_db.session() as ctrl_session,
        ):
            connect_sessions = await CredentialIdentityRepairRepository.list_api_connect_sessions(
                ctrl_session
            )
            if self._ctx.has_db("admin"):
                async with self._ctx.admin_db.session() as adm_session:
                    (
                        api_refs,
                        cred_refs,
                        bound_agents,
                    ) = await CredentialIdentityRepairRepository.load_full_reference_inventory(
                        registry_session=reg_session,
                        control_session=ctrl_session,
                        admin_session=adm_session,
                        apis=apis,
                        credentials=credentials,
                    )
            else:
                (
                    api_refs,
                    cred_refs,
                    bound_agents,
                ) = await CredentialIdentityRepairRepository.load_full_reference_inventory(
                    registry_session=reg_session,
                    control_session=ctrl_session,
                    admin_session=None,
                    apis=apis,
                    credentials=credentials,
                )

        plan = build_repair_plan(
            apis=apis,
            credentials=credentials,
            connect_sessions=connect_sessions,
            manifest_entries=manifest_entries,
            revision_urls_by_api_id=rev_urls,
            api_ref_counts=api_refs,
            credential_ref_counts=cred_refs,
            active_bound_agents_by_credential=bound_agents,
        )

        planned_result = _planned_result(
            plan, apis_scanned=len(apis), credentials_scanned=len(credentials)
        )
        if dry_run:
            return planned_result
        if before_apply is not None:
            await before_apply(planned_result)

        applied_apis: list[PlannedApiRepair] = []
        applied_creds: list[PlannedCredentialRepair] = []
        applied_standalone_sessions: list[PlannedConnectSessionRepair] = []
        applied_findings: list[RepairFinding] = []
        runtime_conflicts: list[RepairFinding] = list(plan.conflicts)
        failed_api_pairs: set[tuple[str, str]] = set()

        standalone_groups: dict[
            tuple[str, str | None, str | None], list[PlannedConnectSessionRepair]
        ] = {}
        for cs_plan in plan.open_session_repairs:
            key = (cs_plan.before_vendor, cs_plan.before_name, cs_plan.before_version)
            standalone_groups.setdefault(key, []).append(cs_plan)
        standalone_ids = {cs_plan.session_id for cs_plan in plan.open_session_repairs}
        for group in standalone_groups.values():
            outcome = await self._apply_standalone_session_group(group)
            if outcome == "repaired":
                applied_standalone_sessions.extend(group)
                applied_findings.extend(
                    _session_repair_finding("repaired", cs_plan) for cs_plan in group
                )
                continue
            failed_api_pairs.update(
                (cs_plan.before_vendor, cs_plan.before_name)
                for cs_plan in group
                if cs_plan.before_name is not None
            )
            runtime_conflicts.extend(
                RepairFinding(
                    category="conflict",
                    surface="control",
                    resource_type="connect_session",
                    resource_id=cs_plan.session_id,
                    shape="open_api_connect_session",
                    reason=outcome,
                    before=cs_plan.before_dict(),
                    after=cs_plan.after_dict(),
                    references={},
                    remediation=(
                        f"Connect-session group containing {cs_plan.session_id} did not commit; "
                        "its dependent API rename was held back. Re-run repair."
                    ),
                )
                for cs_plan in group
            )

        for api_plan in plan.api_repairs:
            pair = (api_plan.before_vendor, api_plan.before_name)
            if pair in failed_api_pairs:
                runtime_conflicts.append(
                    RepairFinding(
                        category="conflict",
                        surface="registry",
                        resource_type="api",
                        resource_id=api_plan.api_id,
                        shape=api_plan.shape,
                        reason="standalone_session_write_failed",
                        before=api_plan.before_dict(),
                        after=api_plan.after_dict(),
                        references=api_plan.references.as_dict(),
                        remediation="Resolve the open-session conflict and re-run repair.",
                    )
                )
                continue
            outcome = await self._apply_api_repair(
                api_plan,
                expected_session_ids=frozenset(
                    cs.id
                    for cs in connect_sessions
                    if cs.state in OPEN_CONNECT_SESSION_STATES
                    and (cs.vendor, cs.api_name, cs.api_version)
                    == (
                        api_plan.before_vendor,
                        api_plan.before_name,
                        api_plan.before_version,
                    )
                    and cs.id not in standalone_ids
                ),
            )
            if outcome == "repaired":
                applied_apis.append(api_plan)
                applied_findings.append(_api_repair_finding("repaired", api_plan))
                logger.info(
                    "registry_api_identity_repaired",
                    api_id=api_plan.api_id,
                    before_name=api_plan.before_name,
                    after_name=api_plan.after_name,
                )
            else:
                failed_api_pairs.add(pair)
                compensation_errors = await self._compensate_standalone_sessions(
                    api_plan, applied_standalone_sessions
                )
                if compensation_errors:
                    runtime_conflicts.append(
                        RepairFinding(
                            category="conflict",
                            surface="control",
                            resource_type="connect_session",
                            resource_id=api_plan.api_id,
                            shape="open_api_connect_session",
                            reason="standalone_session_compensation_incomplete",
                            before=api_plan.before_dict(),
                            after=api_plan.after_dict(),
                            references={},
                            detail={"failed_compensations": compensation_errors},
                            remediation=(
                                "The applied backup records the remaining forward writes. "
                                "Inspect the named sessions and re-run repair."
                            ),
                        )
                    )
                runtime_conflicts.append(
                    RepairFinding(
                        category="conflict",
                        surface="registry",
                        resource_type="api",
                        resource_id=api_plan.api_id,
                        shape=api_plan.shape,
                        reason=outcome,
                        before=api_plan.before_dict(),
                        after=api_plan.after_dict(),
                        references=api_plan.references.as_dict(),
                        remediation=(
                            f"API {api_plan.api_id} changed concurrently or failed to write; "
                            "re-run repair after inspecting current state."
                        ),
                    )
                )

        for cred_plan in plan.credential_repairs:
            cred_pair = (cred_plan.before_vendor, cred_plan.before_name)
            if cred_plan.before_name is not None and cred_pair in failed_api_pairs:
                runtime_conflicts.append(
                    RepairFinding(
                        category="conflict",
                        surface="control",
                        resource_type="credential",
                        resource_id=cred_plan.credential_id,
                        shape=cred_plan.shape,
                        reason="registry_api_write_failed",
                        before=cred_plan.before_dict(),
                        after=cred_plan.after_dict(),
                        references=cred_plan.references.as_dict(),
                        detail={"credential_name": cred_plan.credential_name},
                        remediation=(
                            f"Credential {cred_plan.credential_id} skipped because its paired "
                            "registry API rename did not complete; re-run repair."
                        ),
                    )
                )
                continue

            outcome, _ = await self._apply_credential_repair(cred_plan)
            if outcome == "repaired":
                applied_creds.append(cred_plan)
                applied_findings.append(_credential_repair_finding("repaired", cred_plan))
                self._ctx.invalidate_broker_caches(credential_id=cred_plan.credential_id)
                logger.info(
                    "cred_identity_repaired",
                    cred_id=cred_plan.credential_id,
                    shape=cred_plan.shape,
                    after_vendor=cred_plan.after_vendor,
                    after_name=cred_plan.after_name,
                    after_version=cred_plan.after_version,
                )
                continue

            runtime_conflicts.append(
                RepairFinding(
                    category="conflict",
                    surface="control",
                    resource_type="credential",
                    resource_id=cred_plan.credential_id,
                    shape=cred_plan.shape,
                    reason=outcome,
                    before=cred_plan.before_dict(),
                    after=cred_plan.after_dict(),
                    references=cred_plan.references.as_dict(),
                    detail={"credential_name": cred_plan.credential_name},
                    remediation=(
                        f"Credential {cred_plan.credential_id} changed concurrently or "
                        "failed to write; re-run repair."
                    ),
                )
            )
            paired_api = next(
                (
                    api_plan
                    for api_plan in applied_apis
                    if (api_plan.before_vendor, api_plan.before_name) == cred_pair
                ),
                None,
            )
            if paired_api is None:
                continue
            failed_api_pairs.add((paired_api.before_vendor, paired_api.before_name))
            compensation_errors = await self._compensate_paired_api(
                api_plan=paired_api,
                applied_apis=applied_apis,
                applied_creds=applied_creds,
                applied_standalone_sessions=applied_standalone_sessions,
            )
            if compensation_errors:
                runtime_conflicts.append(
                    RepairFinding(
                        category="conflict",
                        surface="registry",
                        resource_type="api",
                        resource_id=paired_api.api_id,
                        shape=paired_api.shape,
                        reason="paired_credential_compensation_incomplete",
                        before=paired_api.before_dict(),
                        after=paired_api.after_dict(),
                        references=paired_api.references.as_dict(),
                        detail={"failed_compensations": compensation_errors},
                        remediation=(
                            "The run backup records every committed row. Re-run repair to "
                            "reconcile the remaining rows after inspecting current state."
                        ),
                    )
                )
            else:
                runtime_conflicts.append(
                    RepairFinding(
                        category="conflict",
                        surface="registry",
                        resource_type="api",
                        resource_id=paired_api.api_id,
                        shape=paired_api.shape,
                        reason="paired_credential_write_failed_compensated",
                        before=paired_api.before_dict(),
                        after=paired_api.after_dict(),
                        references=paired_api.references.as_dict(),
                        remediation=(
                            "The paired credential write failed and committed API/control rows "
                            "were restored to their pre-run identities. Re-run repair."
                        ),
                    )
                )

        repaired_sessions = sum(
            len(cred_plan.connect_sessions) for cred_plan in applied_creds
        ) + len(applied_standalone_sessions)

        applied_ids = {
            "api": {api.api_id for api in applied_apis},
            "credential": {cred.credential_id for cred in applied_creds},
            "connect_session": {cs.session_id for cs in applied_standalone_sessions}
            | {cs.session_id for cred in applied_creds for cs in cred.connect_sessions},
        }
        applied_findings = [
            finding
            for finding in applied_findings
            if finding.category != "repaired"
            or finding.resource_id in applied_ids.get(finding.resource_type, set())
        ]
        applied_findings.extend(plan.unresolvable)
        applied_findings.extend(runtime_conflicts)
        backup = build_backup_document(
            tool_version=__version__,
            apis=applied_apis,
            credentials=applied_creds,
            standalone_sessions=applied_standalone_sessions,
        )
        return RepairRunResult(
            dry_run=False,
            apis_scanned=len(apis),
            credentials_scanned=len(credentials),
            repaired_apis=len(applied_apis),
            repaired_credentials=len(applied_creds),
            repaired_connect_sessions=repaired_sessions,
            unresolvable_count=len(plan.unresolvable),
            conflict_count=len(runtime_conflicts),
            failed_closed=bool(runtime_conflicts),
            findings=tuple(applied_findings),
            backup=backup,
        )

    async def restore_backup(
        self, document: Mapping[str, Any], *, dry_run: bool = False
    ) -> RestoreOutcome:
        """Reversibly restore rows mutated by a repair run from its backup document.

        Restores control rows (``connect_sessions``, ``credentials``) before
        registry ``apis`` (the reverse of forward repair order). Fails closed with
        :class:`IdentityRepairBackupError` before mutating if any row changed to a
        third value or if restoring an API would collide with another row.
        Idempotent: rows already at their ``before`` state count as
        ``already_restored``.
        """
        async with hold_run_lock(self._ctx, IDENTITY_REPAIR_LOCK_KEY):
            return await self._restore_backup_locked(document, dry_run=dry_run)

    async def _restore_backup_locked(
        self, document: Mapping[str, Any], *, dry_run: bool
    ) -> RestoreOutcome:
        tables = validate_backup_document(document)
        outcome = RestoreOutcome(dry_run=dry_run)

        session_actions: list[dict[str, Any]] = []
        cred_actions: list[dict[str, Any]] = []
        api_actions: list[dict[str, Any]] = []

        # Preflight verification across both DBs before any rollback mutation.
        async with self._ctx.control_db.session() as ctrl_session:
            for entry in tables["connect_sessions"]:
                sid = entry["id"]
                before = entry["before"]
                after = entry["after"]
                snap = await CredentialIdentityRepairRepository.get_connect_session_snapshot(
                    ctrl_session, sid
                )
                if snap is None:
                    raise IdentityRepairBackupError(
                        f"connect_session {sid!r} no longer exists; refusing partial rollback"
                    )
                current = {
                    "vendor": snap.vendor,
                    "api_name": snap.api_name,
                    "api_version": snap.api_version,
                }
                if current == before:
                    outcome.already_restored += 1
                elif current == after:
                    session_actions.append(entry)
                else:
                    raise IdentityRepairBackupError(
                        f"connect_session {sid!r} drifted from expected post-repair state "
                        f"({current} != {after}); refusing unsafe rollback"
                    )

            for entry in tables["credentials"]:
                cid = entry["id"]
                before = entry["before"]
                after = entry["after"]
                cred_snap = await CredentialIdentityRepairRepository.get_credential_snapshot(
                    ctrl_session, cid
                )
                if cred_snap is None:
                    raise IdentityRepairBackupError(
                        f"credential {cid!r} no longer exists; refusing partial rollback"
                    )
                current = {
                    "api_vendor": cred_snap.api_vendor,
                    "api_name": cred_snap.api_name,
                    "api_version": cred_snap.api_version,
                    "catalog_api_id": cred_snap.catalog_api_id,
                }
                if current == before:
                    outcome.already_restored += 1
                elif current == after:
                    cred_actions.append(entry)
                else:
                    raise IdentityRepairBackupError(
                        f"credential {cid!r} drifted from expected post-repair state "
                        f"({current} != {after}); refusing unsafe rollback"
                    )

        async with self._ctx.registry_db.session() as reg_session:
            for entry in tables["apis"]:
                aid = entry["id"]
                before = entry["before"]
                after = entry["after"]
                api_snap = await CredentialIdentityRepairRepository.get_api_snapshot(
                    reg_session, aid
                )
                if api_snap is None:
                    raise IdentityRepairBackupError(
                        f"api {aid!r} no longer exists; refusing partial rollback"
                    )
                current = {
                    "vendor": api_snap.vendor,
                    "name": api_snap.name,
                    "version": api_snap.version,
                    "catalog_api_id": api_snap.catalog_api_id,
                }
                if current == before:
                    outcome.already_restored += 1
                elif current == after:
                    occupant = await CredentialIdentityRepairRepository.find_api_id_by_triple(
                        reg_session,
                        vendor=str(before["vendor"]),
                        name=str(before["name"]),
                        version=str(before["version"]),
                    )
                    if occupant is not None and occupant != aid:
                        raise IdentityRepairBackupError(
                            f"cannot restore api {aid!r} to "
                            f"{before['vendor']}/{before['name']}/{before['version']}: "
                            f"occupied by api {occupant!r}"
                        )
                    api_actions.append(entry)
                else:
                    raise IdentityRepairBackupError(
                        f"api {aid!r} drifted from expected post-repair state "
                        f"({current} != {after}); refusing unsafe rollback"
                    )

        if dry_run:
            outcome.restored_connect_sessions = len(session_actions)
            outcome.restored_credentials = len(cred_actions)
            outcome.restored_apis = len(api_actions)
            return outcome

        applied_entries: list[tuple[str, dict[str, Any]]] = []
        try:
            # Apply rollback in reverse dependency order: sessions -> credentials -> APIs.
            for entry in session_actions:
                sid = entry["id"]
                before = entry["before"]
                after = entry["after"]
                async with self._ctx.control_db.transaction() as ctrl_session:
                    applied = await CredentialIdentityRepairRepository.cas_update_connect_session(
                        ctrl_session, session_id=sid, before=after, after=before
                    )
                    if not applied:
                        raise IdentityRepairBackupError(
                            f"connect_session {sid!r} changed concurrently during rollback"
                        )
                await self._audit(
                    target_type=AuditTargetType.SESSION,
                    target_id=sid,
                    before=after,
                    after=before,
                    reason=_AUDIT_REASON_ROLLBACK,
                )
                applied_entries.append(("connect_session", entry))
                outcome.restored_connect_sessions += 1

            for entry in cred_actions:
                cid = entry["id"]
                before = entry["before"]
                after = entry["after"]
                async with self._ctx.control_db.transaction() as ctrl_session:
                    applied = await CredentialIdentityRepairRepository.cas_update_credential(
                        ctrl_session, credential_id=cid, before=after, after=before
                    )
                    if not applied:
                        raise IdentityRepairBackupError(
                            f"credential {cid!r} changed concurrently during rollback"
                        )
                await self._audit(
                    target_type=AuditTargetType.CREDENTIAL,
                    target_id=cid,
                    before=after,
                    after=before,
                    reason=_AUDIT_REASON_ROLLBACK,
                )
                applied_entries.append(("credential", entry))
                self._ctx.invalidate_broker_caches(credential_id=cid)
                outcome.restored_credentials += 1

            for entry in api_actions:
                aid = entry["id"]
                before = entry["before"]
                after = entry["after"]
                async with self._ctx.registry_db.transaction() as reg_session:
                    applied = await CredentialIdentityRepairRepository.cas_update_api(
                        reg_session, api_id=aid, before=after, after=before
                    )
                    if not applied:
                        raise IdentityRepairBackupError(
                            f"api {aid!r} changed concurrently during rollback"
                        )
                await self._audit(
                    target_type=AuditTargetType.API,
                    target_id=aid,
                    before=after,
                    after=before,
                    reason=_AUDIT_REASON_ROLLBACK,
                )
                applied_entries.append(("api", entry))
                outcome.restored_apis += 1
        except Exception as exc:
            compensation_errors = await self._compensate_restore(applied_entries)
            detail = (
                f"; failed compensation IDs: {compensation_errors}" if compensation_errors else ""
            )
            raise IdentityRepairBackupError(
                f"{exc}; completed rollback writes were compensated{detail}"
            ) from exc

        return outcome

    async def _compensate_restore(
        self, applied_entries: list[tuple[str, dict[str, Any]]]
    ) -> list[str]:
        """Return committed rows to their post-repair state after a restore failure."""
        errors: list[str] = []
        for kind, entry in reversed(applied_entries):
            row_id = str(entry["id"])
            before = entry["before"]
            after = entry["after"]
            try:
                if kind == "api":
                    async with self._ctx.registry_db.transaction() as session:
                        applied = await CredentialIdentityRepairRepository.cas_update_api(
                            session, api_id=row_id, before=before, after=after
                        )
                    target_type = AuditTargetType.API
                elif kind == "credential":
                    async with self._ctx.control_db.transaction() as session:
                        applied = await CredentialIdentityRepairRepository.cas_update_credential(
                            session, credential_id=row_id, before=before, after=after
                        )
                    target_type = AuditTargetType.CREDENTIAL
                    self._ctx.invalidate_broker_caches(credential_id=row_id)
                else:
                    async with self._ctx.control_db.transaction() as session:
                        applied = (
                            await CredentialIdentityRepairRepository.cas_update_connect_session(
                                session, session_id=row_id, before=before, after=after
                            )
                        )
                    target_type = AuditTargetType.SESSION
                if not applied:
                    errors.append(row_id)
                    continue
                await self._audit(
                    target_type=target_type,
                    target_id=row_id,
                    before=before,
                    after=after,
                    reason=_AUDIT_REASON_COMPENSATION,
                )
            except Exception:
                logger.warning("identity_restore_compensation_failed", row_id=row_id, exc_info=True)
                errors.append(row_id)
        return errors

    async def _apply_api_repair(
        self,
        plan: PlannedApiRepair,
        *,
        expected_session_ids: frozenset[str] | None = None,
        compensate: bool = False,
    ) -> str:
        before = plan.after_dict() if compensate else plan.before_dict()
        after = plan.before_dict() if compensate else plan.after_dict()
        try:
            async with self._ctx.control_db.transaction() as ctrl_session:
                if expected_session_ids is not None:
                    current_ids = await CredentialIdentityRepairRepository.list_open_session_ids(
                        ctrl_session,
                        vendor=str(before["vendor"]),
                        name=before["name"],
                        version=before["version"],
                    )
                    if frozenset(current_ids) != expected_session_ids:
                        return "session_inventory_changed"
                async with self._ctx.registry_db.transaction() as reg_session:
                    applied = await CredentialIdentityRepairRepository.cas_update_api(
                        reg_session,
                        api_id=plan.api_id,
                        before=before,
                        after=after,
                    )
                    if not applied:
                        return "changed_concurrently"
            await self._audit(
                target_type=AuditTargetType.API,
                target_id=plan.api_id,
                before=before,
                after=after,
                reason=_AUDIT_REASON_COMPENSATION if compensate else _AUDIT_REASON_REPAIR,
            )
        except Exception:
            logger.warning("registry_api_identity_repair_failed", api_id=plan.api_id, exc_info=True)
            return "write_failed"
        return "repaired"

    async def _apply_credential_repair(
        self, plan: PlannedCredentialRepair, *, compensate: bool = False
    ) -> tuple[str, int]:
        sessions_updated = 0
        before = plan.after_dict() if compensate else plan.before_dict()
        after = plan.before_dict() if compensate else plan.after_dict()
        reason = _AUDIT_REASON_COMPENSATION if compensate else _AUDIT_REASON_REPAIR
        try:
            async with self._ctx.control_db.transaction() as ctrl_session:
                applied = await CredentialIdentityRepairRepository.cas_update_credential(
                    ctrl_session,
                    credential_id=plan.credential_id,
                    before=before,
                    after=after,
                )
                if not applied:
                    return "changed_concurrently", 0
                for cs in plan.connect_sessions:
                    cs_ok = await CredentialIdentityRepairRepository.cas_update_connect_session(
                        ctrl_session,
                        session_id=cs.session_id,
                        before=cs.after_dict() if compensate else cs.before_dict(),
                        after=cs.before_dict() if compensate else cs.after_dict(),
                    )
                    if not cs_ok:
                        raise RuntimeError(f"connect_session {cs.session_id} changed concurrently")
                    sessions_updated += 1
            await self._audit(
                target_type=AuditTargetType.CREDENTIAL,
                target_id=plan.credential_id,
                before=before,
                after=after,
                reason=reason,
            )
            for cs in plan.connect_sessions:
                await self._audit(
                    target_type=AuditTargetType.SESSION,
                    target_id=cs.session_id,
                    before=cs.after_dict() if compensate else cs.before_dict(),
                    after=cs.before_dict() if compensate else cs.after_dict(),
                    reason=reason,
                )
        except Exception:
            logger.warning("cred_identity_repair_failed", cred_id=plan.credential_id, exc_info=True)
            return "write_failed", 0
        return "repaired", sessions_updated

    async def _apply_standalone_session_group(
        self, plans: list[PlannedConnectSessionRepair], *, compensate: bool = False
    ) -> str:
        reason = _AUDIT_REASON_COMPENSATION if compensate else _AUDIT_REASON_REPAIR
        try:
            async with self._ctx.control_db.transaction() as ctrl_session:
                for plan in plans:
                    applied = await CredentialIdentityRepairRepository.cas_update_connect_session(
                        ctrl_session,
                        session_id=plan.session_id,
                        before=plan.after_dict() if compensate else plan.before_dict(),
                        after=plan.before_dict() if compensate else plan.after_dict(),
                    )
                    if not applied:
                        raise RuntimeError(
                            f"connect_session {plan.session_id} changed concurrently"
                        )
        except Exception:
            logger.warning(
                "connect_session_identity_repair_failed",
                session_ids=[plan.session_id for plan in plans],
                exc_info=True,
            )
            return "write_failed"
        for plan in plans:
            await self._audit(
                target_type=AuditTargetType.SESSION,
                target_id=plan.session_id,
                before=plan.after_dict() if compensate else plan.before_dict(),
                after=plan.before_dict() if compensate else plan.after_dict(),
                reason=reason,
            )
        return "repaired"

    async def _compensate_standalone_sessions(
        self,
        api_plan: PlannedApiRepair,
        applied_standalone_sessions: list[PlannedConnectSessionRepair],
    ) -> list[str]:
        """Return standalone sessions to their source target when their API does not move."""
        standalone = [
            plan
            for plan in applied_standalone_sessions
            if (plan.before_vendor, plan.before_name, plan.before_version)
            == (
                api_plan.before_vendor,
                api_plan.before_name,
                api_plan.before_version,
            )
        ]
        if not standalone:
            return []
        outcome = await self._apply_standalone_session_group(standalone, compensate=True)
        if outcome != "repaired":
            return [plan.session_id for plan in standalone]
        for plan in standalone:
            applied_standalone_sessions.remove(plan)
        return []

    async def _compensate_paired_api(
        self,
        *,
        api_plan: PlannedApiRepair,
        applied_apis: list[PlannedApiRepair],
        applied_creds: list[PlannedCredentialRepair],
        applied_standalone_sessions: list[PlannedConnectSessionRepair],
    ) -> list[str]:
        """Return IDs whose committed forward repair could not be compensated."""
        errors: list[str] = []
        for cred_plan in reversed(list(applied_creds)):
            if (cred_plan.before_vendor, cred_plan.before_name) != (
                api_plan.before_vendor,
                api_plan.before_name,
            ):
                continue
            outcome, _ = await self._apply_credential_repair(cred_plan, compensate=True)
            if outcome == "repaired":
                applied_creds.remove(cred_plan)
                self._ctx.invalidate_broker_caches(credential_id=cred_plan.credential_id)
            else:
                errors.append(cred_plan.credential_id)

        standalone = [
            plan
            for plan in applied_standalone_sessions
            if (plan.before_vendor, plan.before_name, plan.before_version)
            == (
                api_plan.before_vendor,
                api_plan.before_name,
                api_plan.before_version,
            )
        ]
        if standalone:
            outcome = await self._apply_standalone_session_group(standalone, compensate=True)
            if outcome == "repaired":
                for plan in standalone:
                    applied_standalone_sessions.remove(plan)
            else:
                errors.extend(plan.session_id for plan in standalone)

        if not errors:
            outcome = await self._apply_api_repair(api_plan, compensate=True)
            if outcome == "repaired":
                applied_apis.remove(api_plan)
            else:
                errors.append(api_plan.api_id)
        return errors

    async def _audit(
        self,
        *,
        target_type: AuditTargetType,
        target_id: str,
        before: dict[str, Any],
        after: dict[str, Any],
        reason: str,
    ) -> None:
        if not self._ctx.has_db("admin"):
            return
        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.UPDATE,
            target_type=target_type,
            target_id=target_id,
            actor_type=_AUDIT_ACTOR_TYPE,
            actor_id=_AUDIT_ACTOR_ID,
            before=before,
            after=after,
            reason=reason,
            origin=None,
        )


def _planned_result(
    plan: RepairPlan, *, apis_scanned: int, credentials_scanned: int
) -> RepairRunResult:
    """Describe one locked plan for dry-run output and pre-apply persistence."""
    findings = [_api_repair_finding("would_repair", item) for item in plan.api_repairs]
    findings.extend(
        _credential_repair_finding("would_repair", item) for item in plan.credential_repairs
    )
    findings.extend(
        _session_repair_finding("would_repair", item) for item in plan.open_session_repairs
    )
    findings.extend(plan.unresolvable)
    findings.extend(plan.conflicts)
    return RepairRunResult(
        dry_run=True,
        apis_scanned=apis_scanned,
        credentials_scanned=credentials_scanned,
        repaired_apis=len(plan.api_repairs),
        repaired_credentials=len(plan.credential_repairs),
        repaired_connect_sessions=len(plan.open_session_repairs)
        + sum(len(item.connect_sessions) for item in plan.credential_repairs),
        unresolvable_count=len(plan.unresolvable),
        conflict_count=len(plan.conflicts),
        failed_closed=bool(plan.conflicts),
        findings=tuple(findings),
        backup=build_backup_document(
            tool_version=__version__,
            apis=plan.api_repairs,
            credentials=plan.credential_repairs,
            standalone_sessions=plan.open_session_repairs,
        ),
    )


def _api_repair_finding(category: str, plan: PlannedApiRepair) -> RepairFinding:
    verb = "Would repair" if category == "would_repair" else "Repaired"
    return RepairFinding(
        category=category,
        surface="registry",
        resource_type="api",
        resource_id=plan.api_id,
        shape=plan.shape,
        reason="unique_safe_mapping",
        before=plan.before_dict(),
        after=plan.after_dict(),
        references=plan.references.as_dict(),
        remediation=(
            f"{verb} registry API {plan.api_id} in place from "
            f"'{plan.before_vendor}/{plan.before_name}/{plan.before_version}' to "
            f"'{plan.after_vendor}/{plan.after_name}/{plan.after_version}' "
            "(revisions, operations, overlays, and notes stay attached; "
            "execution_records remain immutable)."
        ),
    )


def _credential_repair_finding(category: str, plan: PlannedCredentialRepair) -> RepairFinding:
    verb = "Would repair" if category == "would_repair" else "Repaired"
    return RepairFinding(
        category=category,
        surface="control",
        resource_type="credential",
        resource_id=plan.credential_id,
        shape=plan.shape,
        reason="unique_safe_mapping",
        before=plan.before_dict(),
        after=plan.after_dict(),
        references=plan.references.as_dict(),
        detail={
            "credential_name": plan.credential_name,
            "coupled_open_connect_sessions": [cs.session_id for cs in plan.connect_sessions],
        },
        remediation=(
            f"{verb} credential {plan.credential_id} ({plan.credential_name!r}) in place from "
            f"'{plan.before_vendor}/{plan.before_name or '*'}/{plan.before_version or '*'}' to "
            f"'{plan.after_vendor}/{plan.after_name or '*'}/{plan.after_version or '*'}' "
            "(bindings, permission rules, and encrypted secrets stay attached; "
            "execution_records remain immutable)."
        ),
    )


def _session_repair_finding(category: str, plan: PlannedConnectSessionRepair) -> RepairFinding:
    verb = "Would repair" if category == "would_repair" else "Repaired"
    return RepairFinding(
        category=category,
        surface="control",
        resource_type="connect_session",
        resource_id=plan.session_id,
        shape="open_api_connect_session",
        reason="unique_safe_mapping",
        before=plan.before_dict(),
        after=plan.after_dict(),
        references={},
        detail={"credential_id": plan.credential_id},
        remediation=(
            f"{verb} open connect session {plan.session_id} target API identity in place."
        ),
    )
