"""Rewrite binding rules written against the full upstream path (#1424).

Binding permission rules are enforced on the spec-relative request path — the
path relative to the API's server URL, which every rule-authoring surface
shows. A ``prefix``/``exact`` rule written with the server base path baked in
(``/api/v3/pet`` for a server ``https://host/api/v3``) matches no request on
that basis: an ``allow`` stops allowing and — worse — a ``deny`` stops
denying, letting a later broader ``allow`` through.

This operator-run job (``jentic_one rewrite-rule-base-paths``) finds such rules
and rewrites them to their spec-relative form. The decision per rule is
``shared.permissions.base_path_rewrite.decide_rewrite``, which only rewrites
a static base path when the result is unambiguous and applies to a real
operation; everything else (regex rules, server-variable bases, ambiguous
bases, a shared rule set whose APIs disagree, an API with no live revision,
vendor-wide credentials) is reported for the operator to fix by hand.

Idempotent: a rewritten path is spec-relative, so a re-run leaves it alone.
``--diff-only`` reports without writing. Each rewrite is compare-and-set on
the old path (a concurrent edit wins) and audited — the audit entry gates the
write, so no rule changes without its record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from jentic_one.control.repos.rule_base_path_rewrite_repo import (
    ApiIdentity,
    RewriteRuleAdminRepository,
    RewriteRuleControlRepository,
    RuleRow,
)
from jentic_one.shared.audit import record_audit
from jentic_one.shared.context import Context
from jentic_one.shared.models.audit import AuditAction, AuditTargetType
from jentic_one.shared.permissions.base_path_rewrite import (
    ApiPathShape,
    ApiPathShapeReaderProtocol,
    RewriteDecision,
    RewriteOutcome,
    SkipReason,
    decide_rewrite,
)

logger = structlog.get_logger(__name__)

#: ``audit_entries.actor_type`` / ``actor_id`` for job-derived writes (jobs are
#: not authenticated actors; the audit read surface treats both as opaque).
_AUDIT_ACTOR_TYPE = "system:job"
_AUDIT_ACTOR_ID = "system:rewrite-rule-base-paths"
_AUDIT_REASON = "rule_base_path_rewrite"


@dataclass
class RewriteFinding:
    """One JSONL report line."""

    category: str  # "rewritten" | "would_rewrite" | "skipped" | "conflict"
    detail: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"category": self.category, **self.detail}


@dataclass
class RewriteRunResult:
    diff_only: bool
    rules_scanned: int = 0
    rewritten: int = 0
    skipped: int = 0
    findings: list[RewriteFinding] = field(default_factory=list)


class RuleBasePathRewriteService:
    """Finds and rewrites base-path-qualified binding rules."""

    def __init__(self, ctx: Context, *, shapes: ApiPathShapeReaderProtocol) -> None:
        self._ctx = ctx
        self._shapes = shapes
        self._shape_cache: dict[ApiIdentity, ApiPathShape | None] = {}

    async def _shape(self, api: ApiIdentity) -> ApiPathShape | None:
        if api not in self._shape_cache:
            self._shape_cache[api] = await self._shapes.get(
                vendor=api.vendor, name=api.name, version=api.version
            )
        return self._shape_cache[api]

    async def run(self, *, diff_only: bool) -> RewriteRunResult:
        result = RewriteRunResult(diff_only=diff_only)
        # Three bulk reads up front — no per-rule queries.
        async with self._ctx.control_db.session() as session:
            rules = await RewriteRuleControlRepository.list_path_rules(session)
            credential_apis = await RewriteRuleControlRepository.credential_apis(session)
        async with self._ctx.admin_db.session() as session:
            bindings = await RewriteRuleAdminRepository.bindings(session)
        binding_ids = {(b.agent_id, b.credential_id): b.binding_id for b in bindings}
        rule_set_credentials: dict[str, set[str]] = {}
        for b in bindings:
            if b.rule_set_id is not None:
                rule_set_credentials.setdefault(b.rule_set_id, set()).add(b.credential_id)

        for rule in rules:
            result.rules_scanned += 1
            detail: dict[str, Any] = {
                "rule_id": rule.rule_id,
                "source": rule.source,
                "owner": rule.owner_key,
                "match_mode": rule.match_mode,
                "old_path": rule.path,
            }
            binding_id: str | None = None
            if rule.source == "binding":
                assert rule.agent_id is not None and rule.credential_id is not None
                binding_id = binding_ids.get((rule.agent_id, rule.credential_id))
                detail["binding_id"] = binding_id
                credential_ids = [rule.credential_id]
            else:
                credential_ids = sorted(rule_set_credentials.get(rule.owner_key, set()))

            decision, apis = await self._decide(
                rule, binding_id=binding_id, credential_ids=credential_ids, apis=credential_apis
            )
            detail["apis"] = [f"{a.vendor}/{a.name}/{a.version}" for a in apis]

            if decision.outcome is RewriteOutcome.UNCHANGED:
                continue
            if decision.outcome is RewriteOutcome.SKIPPED:
                assert decision.reason is not None
                result.skipped += 1
                result.findings.append(
                    RewriteFinding("skipped", {**detail, "reason": decision.reason.value})
                )
                continue

            assert decision.new_path is not None
            detail["new_path"] = decision.new_path
            if diff_only:
                result.rewritten += 1
                result.findings.append(RewriteFinding("would_rewrite", detail))
                continue
            outcome = await self._apply(rule, new_path=decision.new_path, binding_id=binding_id)
            if outcome != "rewritten":
                result.skipped += 1
                result.findings.append(RewriteFinding("conflict", {**detail, "reason": outcome}))
                continue
            result.rewritten += 1
            result.findings.append(RewriteFinding("rewritten", detail))
            logger.info("rule_base_path_rewritten", **detail)
        return result

    async def _decide(
        self,
        rule: RuleRow,
        *,
        binding_id: str | None,
        credential_ids: list[str],
        apis: dict[str, ApiIdentity | None],
    ) -> tuple[RewriteDecision, list[ApiIdentity]]:
        """The decision for one rule plus the APIs it was judged against."""
        if rule.source == "binding" and binding_id is None:
            # Rules of a removed binding are never evaluated; nothing to audit against.
            return RewriteDecision.skipped(SkipReason.BINDING_NOT_FOUND), []
        if not credential_ids:
            return RewriteDecision.skipped(SkipReason.RULE_SET_NOT_ATTACHED), []
        resolved: list[ApiIdentity] = []
        for credential_id in credential_ids:
            if credential_id not in apis:
                return RewriteDecision.skipped(SkipReason.CREDENTIAL_NOT_FOUND), resolved
            api = apis[credential_id]
            if api is None:
                return RewriteDecision.skipped(SkipReason.CREDENTIAL_NOT_API_SCOPED), resolved
            if api not in resolved:
                resolved.append(api)
        shapes = [await self._shape(api) for api in resolved]
        return decide_rewrite(rule.path, rule.match_mode, shapes), resolved

    async def _apply(self, rule: RuleRow, *, new_path: str, binding_id: str | None) -> str:
        """Compare-and-set the rule's path and audit it; the audit gates the write.

        The audit entry commits (admin DB) *inside* the open control
        transaction: if it fails, the rule update rolls back, so a policy
        change is never left without its audit record. Returns
        ``"rewritten"``, ``"changed_concurrently"`` or ``"write_failed"``.
        """
        try:
            async with self._ctx.control_db.transaction() as control_session:
                applied = await RewriteRuleControlRepository.set_rule_path(
                    control_session,
                    source=rule.source,
                    rule_id=rule.rule_id,
                    old_path=rule.path,
                    new_path=new_path,
                )
                if not applied:
                    return "changed_concurrently"
                async with self._ctx.admin_db.transaction() as admin_session:
                    await record_audit(
                        admin_session,
                        action=AuditAction.UPDATE,
                        target_type=(
                            AuditTargetType.CREDENTIAL_BINDING
                            if rule.source == "binding"
                            else AuditTargetType.PERMISSION_RULE_SET
                        ),
                        target_id=binding_id if binding_id is not None else rule.owner_key,
                        target_parent_id=rule.agent_id,
                        actor_type=_AUDIT_ACTOR_TYPE,
                        actor_id=_AUDIT_ACTOR_ID,
                        before={"rule_id": rule.rule_id, "path": rule.path},
                        after={"rule_id": rule.rule_id, "path": new_path},
                        reason=_AUDIT_REASON,
                        # Job-derived write: no request, so no request origin.
                        origin=None,
                    )
        except Exception:
            # The update and its audit roll back together; report and move on.
            logger.warning("rule_base_path_rewrite_failed", rule_id=rule.rule_id, exc_info=True)
            return "write_failed"
        return "rewritten"
