"""Report stored permission rules whose path no longer passes validation.

A rule row saved before the path validator tightened (or before it existed)
can hold a pattern that ``validate_path`` now refuses. Such a row compiles to
a matcher that never matches: an ``allow`` rule stops granting, a ``deny``
rule denies every request in its method scope, and a ``require-approval`` rule
halts the call as default-deny. The report lists those rows with their effect
so an operator can rewrite them. It reads both rule tables and changes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from jentic_one.control.repos.agent_permission_rule_repo import AgentPermissionRuleRepository
from jentic_one.control.repos.permission_rule_set_repo import PermissionRuleSetRepository
from jentic_one.control.repos.rule_base_path_rewrite_repo import (
    RewriteRuleControlRepository,
    RuleRow,
)
from jentic_one.shared.context import Context
from jentic_one.shared.permissions.matching import validate_path


@dataclass(frozen=True)
class UnsafeRule:
    """One stored rule whose path fails validation."""

    rule_id: str
    #: ``binding`` (inline rule on an agent/credential binding) or ``rule_set``.
    source: str
    #: ``agent_id:credential_id`` for a binding rule, the rule set id otherwise.
    owner_key: str
    #: ``allow`` | ``deny`` | ``require-approval``; ``unknown`` if the row vanished mid-run.
    effect: str
    #: The validation code (``unsafe_regex``, ``invalid_regex``, ...).
    code: str
    match_mode: str


@dataclass(frozen=True)
class UnsafePermissionRuleReport:
    """Every stored rule failing path validation, in id order."""

    rules: tuple[UnsafeRule, ...] = ()


async def _effects_by_rule_id(ctx: Context, failing: list[tuple[RuleRow, str]]) -> dict[str, str]:
    """Rule id -> effect for the owners of the failing rows."""
    effects: dict[str, str] = {}
    seen: set[tuple[str, str]] = set()
    async with ctx.control_db.session() as session:
        for row, _code in failing:
            key = (row.source, row.owner_key)
            if key in seen:
                continue
            seen.add(key)
            if row.source == "binding" and row.agent_id and row.credential_id:
                binding_rules = await AgentPermissionRuleRepository.list_rules(
                    session, row.agent_id, row.credential_id
                )
                effects.update({r.id: r.effect for r in binding_rules})
            else:
                set_rules = await PermissionRuleSetRepository.list_rules(session, row.owner_key)
                effects.update({r.id: r.effect for r in set_rules})
    return effects


async def find_unsafe_permission_rules(ctx: Context) -> UnsafePermissionRuleReport:
    """List the stored rules whose ``(path, match_mode)`` fails ``validate_path``."""
    async with ctx.control_db.session() as session:
        rows = await RewriteRuleControlRepository.list_path_rules(session)
    failing: list[tuple[RuleRow, str]] = []
    for row in rows:
        error = validate_path(row.path, row.match_mode)
        if error is not None:
            failing.append((row, error.code))
    if not failing:
        return UnsafePermissionRuleReport()
    effects = await _effects_by_rule_id(ctx, failing)
    return UnsafePermissionRuleReport(
        rules=tuple(
            UnsafeRule(
                rule_id=row.rule_id,
                source=row.source,
                owner_key=row.owner_key,
                effect=effects.get(row.rule_id, "unknown"),
                code=code,
                match_mode=row.match_mode,
            )
            for row, code in failing
        )
    )
