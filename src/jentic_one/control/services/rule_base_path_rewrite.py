"""Rewrite binding rules written against the full upstream path (#1424).

Binding permission rules are enforced on the spec-relative request path — the
path relative to the API's server URL, which every rule-authoring surface
shows. A ``prefix``/``exact`` rule written with the server base path baked in
(``/eu/widgets`` for a server ``http://host/{region}``) matches no request on
that basis, so its binding denies what the operator meant to allow.

This operator-run job (``jentic_one rewrite-rule-base-paths``) finds such rules
and rewrites them to their spec-relative form. The decision per rule is
``shared.permissions.base_path_rewrite.decide_rewrite``, which only rewrites
when the result is unambiguous and applies to a real operation; everything
else (regex rules, ambiguous bases, a shared rule set whose APIs disagree, an
API with no live revision) is reported for the operator to fix by hand.

Idempotent: a rewritten path is spec-relative, so a re-run leaves it alone.
``--diff-only`` reports without writing. Each rewrite is compare-and-set on
the old path (a concurrent edit wins) and audited.
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
from jentic_one.shared.audit import record_audit_best_effort
from jentic_one.shared.context import Context
from jentic_one.shared.models.audit import AuditAction, AuditTargetType
from jentic_one.shared.permissions.base_path_rewrite import (
    ApiPathShape,
    ApiPathShapeReaderProtocol,
    RewriteOutcome,
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

    async def _apis_for(self, rule: RuleRow) -> list[ApiIdentity | None]:
        """Every API the rule applies to (``None`` = credential names no single API)."""
        if rule.source == "binding":
            credential_ids = [rule.owner_key.split(":", 1)[1]]
        else:
            async with self._ctx.admin_db.session() as session:
                credential_ids = await RewriteRuleAdminRepository.rule_set_credential_ids(
                    session, rule.owner_key
                )
        apis: list[ApiIdentity | None] = []
        async with self._ctx.control_db.session() as session:
            for credential_id in credential_ids:
                api = await RewriteRuleControlRepository.credential_api(session, credential_id)
                if api not in apis:
                    apis.append(api)
        return apis

    async def run(self, *, diff_only: bool) -> RewriteRunResult:
        result = RewriteRunResult(diff_only=diff_only)
        async with self._ctx.control_db.session() as session:
            rules = await RewriteRuleControlRepository.list_path_rules(session)

        for rule in rules:
            result.rules_scanned += 1
            apis = await self._apis_for(rule)
            shapes = [await self._shape(api) if api is not None else None for api in apis]
            decision = decide_rewrite(rule.path, rule.match_mode, shapes)
            detail: dict[str, Any] = {
                "rule_id": rule.rule_id,
                "source": rule.source,
                "owner": rule.owner_key,
                "match_mode": rule.match_mode,
                "old_path": rule.path,
                "apis": [f"{a.vendor}/{a.name}/{a.version}" if a else None for a in apis],
            }

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
            async with self._ctx.control_db.transaction() as session:
                applied = await RewriteRuleControlRepository.set_rule_path(
                    session,
                    source=rule.source,
                    rule_id=rule.rule_id,
                    old_path=rule.path,
                    new_path=decision.new_path,
                )
            if not applied:
                result.skipped += 1
                result.findings.append(
                    RewriteFinding("conflict", {**detail, "reason": "changed_concurrently"})
                )
                continue
            result.rewritten += 1
            result.findings.append(RewriteFinding("rewritten", detail))
            logger.info("rule_base_path_rewritten", **detail)
            await record_audit_best_effort(
                self._ctx,
                action=AuditAction.UPDATE,
                target_type=(
                    AuditTargetType.CREDENTIAL_BINDING
                    if rule.source == "binding"
                    else AuditTargetType.PERMISSION_RULE_SET
                ),
                target_id=rule.owner_key,
                actor_type=_AUDIT_ACTOR_TYPE,
                actor_id=_AUDIT_ACTOR_ID,
                before={"rule_id": rule.rule_id, "path": rule.path},
                after={"rule_id": rule.rule_id, "path": decision.new_path},
                reason=_AUDIT_REASON,
                # Job-derived write: no request, so no request origin.
                origin=None,
            )
        return result
