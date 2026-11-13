"""Cross-database repair service for vendor-doubled (#1079) and path-derived (#1388) identities.

Coordinates ``registry_db``, ``control_db``, and ``admin_db`` so existing
malformed rows are analysed with full cross-surface reference accounting
(revisions, operations, overlays, notes, agent bindings, agent auth keys,
permission rules, connect sessions, held executions, and append-only execution
history) before any rewrite runs:

- Registry ``apis`` rows are updated in place first (preserving ``apis.id`` so
  revisions, operations, URL indexes, overlays, and notes stay attached).
- Control ``credentials`` and open ``api``-target ``connect_sessions`` are
  updated in place next (preserving ``credentials.id`` so encrypted secret rows,
  ``agent_credential_bindings``, ``agent_permission_rules``, and historical
  ``execution_records`` references stay intact).
- Historical ``execution_records`` and terminal ``held_executions`` /
  ``connect_sessions`` remain immutable.
- Colliding or ambiguous rows fail closed without mutating data and surface
  structured operator remediation.
- Every run produces a self-describing, secret-free backup document that
  :meth:`CredentialIdentityRepairService.restore_backup` rolls back idempotently.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import structlog

from jentic_one import __version__
from jentic_one.control.repos.credential_identity_repair_repo import (
    CredentialIdentityRepairRepository,
)
from jentic_one.shared.audit import record_audit
from jentic_one.shared.context import Context
from jentic_one.shared.models.audit import AuditAction, AuditTargetType
from jentic_one.shared.models.credential_identity_repair import (
    IdentityRepairBackupError,
    PlannedApiRepair,
    PlannedConnectSessionRepair,
    PlannedCredentialRepair,
    RepairFinding,
    RepairRunResult,
    RestoreOutcome,
    build_backup_document,
    build_repair_plan,
    has_candidate_anomalies,
    validate_backup_document,
)

logger = structlog.get_logger(__name__)

_AUDIT_ACTOR_TYPE = "system:job"
_AUDIT_ACTOR_ID = "system:credential-identity-repair"
_AUDIT_REASON_REPAIR = "credential_identity_repair"
_AUDIT_REASON_ROLLBACK = "credential_identity_repair_rollback"


class CredentialIdentityRepairService:
    """Analyses, repairs, backs up, and restores malformed API/credential identities."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def run(self, *, dry_run: bool = False) -> RepairRunResult:
        """Inventory malformed rows across registry/control/admin and repair safe ones."""
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

        # Fast-path exit when every API and credential is already canonical and covered:
        # avoids unnecessary admin-DB queries on clean upgrades.
        if not has_candidate_anomalies(
            apis=apis,
            credentials=credentials,
            manifest_entries=manifest_entries,
            revision_urls_by_api_id=rev_urls,
        ):
            empty_backup = build_backup_document(tool_version=__version__, apis=(), credentials=())
            return RepairRunResult(
                dry_run=dry_run,
                apis_scanned=len(apis),
                credentials_scanned=len(credentials),
                repaired_apis=0,
                repaired_credentials=0,
                repaired_connect_sessions=0,
                unresolvable_count=0,
                conflict_count=0,
                failed_closed=False,
                findings=(),
                backup=empty_backup,
            )

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

        if dry_run:
            findings: list[RepairFinding] = []
            session_count = len(plan.open_session_repairs) + sum(
                len(c.connect_sessions) for c in plan.credential_repairs
            )
            for api_plan in plan.api_repairs:
                findings.append(_api_repair_finding("would_repair", api_plan))
            for cred_plan in plan.credential_repairs:
                findings.append(_credential_repair_finding("would_repair", cred_plan))
            for cs_plan in plan.open_session_repairs:
                findings.append(_session_repair_finding("would_repair", cs_plan))
            findings.extend(plan.unresolvable)
            findings.extend(plan.conflicts)
            backup = build_backup_document(
                tool_version=__version__,
                apis=plan.api_repairs,
                credentials=plan.credential_repairs,
                standalone_sessions=plan.open_session_repairs,
            )
            return RepairRunResult(
                dry_run=True,
                apis_scanned=len(apis),
                credentials_scanned=len(credentials),
                repaired_apis=len(plan.api_repairs),
                repaired_credentials=len(plan.credential_repairs),
                repaired_connect_sessions=session_count,
                unresolvable_count=len(plan.unresolvable),
                conflict_count=len(plan.conflicts),
                failed_closed=bool(plan.conflicts),
                findings=tuple(findings),
                backup=backup,
            )

        applied_apis: list[PlannedApiRepair] = []
        applied_creds: list[PlannedCredentialRepair] = []
        applied_standalone_sessions: list[PlannedConnectSessionRepair] = []
        applied_findings: list[RepairFinding] = []
        runtime_conflicts: list[RepairFinding] = list(plan.conflicts)
        failed_api_pairs: set[tuple[str, str]] = set()
        repaired_sessions = 0

        for api_plan in plan.api_repairs:
            outcome = await self._apply_api_repair(api_plan)
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
                failed_api_pairs.add((api_plan.before_vendor, api_plan.before_name))
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
            if (
                cred_plan.before_name is not None
                and (cred_plan.before_vendor, cred_plan.before_name) in failed_api_pairs
            ):
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

            outcome, sessions_updated = await self._apply_credential_repair(cred_plan)
            if outcome == "repaired":
                applied_creds.append(cred_plan)
                repaired_sessions += sessions_updated
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
            else:
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

        for cs_plan in plan.open_session_repairs:
            if (
                cs_plan.before_name is not None
                and (cs_plan.before_vendor, cs_plan.before_name) in failed_api_pairs
            ):
                continue
            cs_outcome = await self._apply_standalone_session_repair(cs_plan)
            if cs_outcome == "repaired":
                applied_standalone_sessions.append(cs_plan)
                repaired_sessions += 1
                applied_findings.append(_session_repair_finding("repaired", cs_plan))
            else:
                runtime_conflicts.append(
                    RepairFinding(
                        category="conflict",
                        surface="control",
                        resource_type="connect_session",
                        resource_id=cs_plan.session_id,
                        shape="open_api_connect_session",
                        reason=cs_outcome,
                        before=cs_plan.before_dict(),
                        after=cs_plan.after_dict(),
                        references={},
                        remediation=(
                            f"Connect session {cs_plan.session_id} changed concurrently; "
                            "re-run repair."
                        ),
                    )
                )

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

        # Apply rollback in reverse dependency order: connect_sessions -> credentials -> apis.
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
            outcome.restored_apis += 1

        return outcome

    async def _apply_api_repair(self, plan: PlannedApiRepair) -> str:
        try:
            async with self._ctx.registry_db.transaction() as reg_session:
                applied = await CredentialIdentityRepairRepository.cas_update_api(
                    reg_session,
                    api_id=plan.api_id,
                    before=plan.before_dict(),
                    after=plan.after_dict(),
                )
                if not applied:
                    return "changed_concurrently"
                await self._audit(
                    target_type=AuditTargetType.API,
                    target_id=plan.api_id,
                    before=plan.before_dict(),
                    after=plan.after_dict(),
                    reason=_AUDIT_REASON_REPAIR,
                )
        except Exception:
            logger.warning("registry_api_identity_repair_failed", api_id=plan.api_id, exc_info=True)
            return "write_failed"
        return "repaired"

    async def _apply_credential_repair(self, plan: PlannedCredentialRepair) -> tuple[str, int]:
        sessions_updated = 0
        try:
            async with self._ctx.control_db.transaction() as ctrl_session:
                applied = await CredentialIdentityRepairRepository.cas_update_credential(
                    ctrl_session,
                    credential_id=plan.credential_id,
                    before=plan.before_dict(),
                    after=plan.after_dict(),
                )
                if not applied:
                    return "changed_concurrently", 0
                for cs in plan.connect_sessions:
                    cs_ok = await CredentialIdentityRepairRepository.cas_update_connect_session(
                        ctrl_session,
                        session_id=cs.session_id,
                        before=cs.before_dict(),
                        after=cs.after_dict(),
                    )
                    if not cs_ok:
                        raise RuntimeError(f"connect_session {cs.session_id} changed concurrently")
                    sessions_updated += 1
                await self._audit(
                    target_type=AuditTargetType.CREDENTIAL,
                    target_id=plan.credential_id,
                    before=plan.before_dict(),
                    after=plan.after_dict(),
                    reason=_AUDIT_REASON_REPAIR,
                )
                for cs in plan.connect_sessions:
                    await self._audit(
                        target_type=AuditTargetType.SESSION,
                        target_id=cs.session_id,
                        before=cs.before_dict(),
                        after=cs.after_dict(),
                        reason=_AUDIT_REASON_REPAIR,
                    )
        except Exception:
            logger.warning("cred_identity_repair_failed", cred_id=plan.credential_id, exc_info=True)
            return "write_failed", 0
        return "repaired", sessions_updated

    async def _apply_standalone_session_repair(self, plan: PlannedConnectSessionRepair) -> str:
        try:
            async with self._ctx.control_db.transaction() as ctrl_session:
                applied = await CredentialIdentityRepairRepository.cas_update_connect_session(
                    ctrl_session,
                    session_id=plan.session_id,
                    before=plan.before_dict(),
                    after=plan.after_dict(),
                )
                if not applied:
                    return "changed_concurrently"
                await self._audit(
                    target_type=AuditTargetType.SESSION,
                    target_id=plan.session_id,
                    before=plan.before_dict(),
                    after=plan.after_dict(),
                    reason=_AUDIT_REASON_REPAIR,
                )
        except Exception:
            logger.warning(
                "connect_session_identity_repair_failed",
                session_id=plan.session_id,
                exc_info=True,
            )
            return "write_failed"
        return "repaired"

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
        async with self._ctx.admin_db.transaction() as admin_session:
            await record_audit(
                admin_session,
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
