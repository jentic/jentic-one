"""Reads and writes for the ``rewrite-rule-base-paths`` job (#1424).

Rules are read from the control DB; the binding → rule-set pointers and the
agent ids that tie rules to bindings live in the admin DB and are read with
raw SQL (the control module never imports admin ORM models — the same seam
``prerequisite_repo`` uses).
"""

from __future__ import annotations

from typing import NamedTuple

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.permission_rule_sets import PermissionRuleSetRule


class RuleRow(NamedTuple):
    """A ``prefix``/``exact``/``regex`` rule with a path, from either rule table."""

    rule_id: str
    source: str  # "binding" | "rule_set"
    owner_key: str  # "agent_id:credential_id" for a binding rule, the rule_set_id otherwise
    path: str
    match_mode: str


class ApiIdentity(NamedTuple):
    vendor: str
    name: str
    version: str


class RewriteRuleControlRepository:
    """Control-DB reads/writes for the rewrite."""

    @staticmethod
    async def list_path_rules(session: AsyncSession) -> list[RuleRow]:
        rows: list[RuleRow] = []
        inline = await session.execute(
            select(
                AgentPermissionRule.id,
                AgentPermissionRule.agent_id,
                AgentPermissionRule.credential_id,
                AgentPermissionRule.path,
                AgentPermissionRule.match_mode,
            )
            .where(AgentPermissionRule.path.is_not(None))
            .order_by(AgentPermissionRule.id)
        )
        for rule_id, agent_id, credential_id, path, mode in inline.all():
            rows.append(
                RuleRow(rule_id, "binding", f"{agent_id}:{credential_id}", path, mode or "regex")
            )
        shared = await session.execute(
            select(
                PermissionRuleSetRule.id,
                PermissionRuleSetRule.rule_set_id,
                PermissionRuleSetRule.path,
                PermissionRuleSetRule.match_mode,
            )
            .where(PermissionRuleSetRule.path.is_not(None))
            .order_by(PermissionRuleSetRule.id)
        )
        for rule_id, rule_set_id, path, mode in shared.all():
            rows.append(RuleRow(rule_id, "rule_set", rule_set_id, path, mode or "regex"))
        return rows

    @staticmethod
    async def credential_api(session: AsyncSession, credential_id: str) -> ApiIdentity | None:
        """The API a credential targets, or ``None`` when it names no single API."""
        row = (
            await session.execute(
                select(Credential.api_vendor, Credential.api_name, Credential.api_version).where(
                    Credential.id == credential_id
                )
            )
        ).first()
        if row is None or not row[0] or not row[1] or not row[2]:
            return None
        return ApiIdentity(row[0], row[1], row[2])

    @staticmethod
    async def set_rule_path(
        session: AsyncSession, *, source: str, rule_id: str, old_path: str, new_path: str
    ) -> bool:
        """Compare-and-set one rule's path; ``False`` if it changed underneath us."""
        model = AgentPermissionRule if source == "binding" else PermissionRuleSetRule
        result = await session.execute(
            update(model).where(model.id == rule_id, model.path == old_path).values(path=new_path)
        )
        return bool(getattr(result, "rowcount", 0))


_RULE_SET_BINDINGS_SQL = text(
    "SELECT credential_id FROM agent_credential_bindings WHERE rule_set_id = :rule_set_id"
)


class RewriteRuleAdminRepository:
    """Admin-DB reads for the rewrite (raw SQL — no admin ORM import)."""

    @staticmethod
    async def rule_set_credential_ids(session: AsyncSession, rule_set_id: str) -> list[str]:
        rows = await session.execute(_RULE_SET_BINDINGS_SQL, {"rule_set_id": rule_set_id})
        return sorted({row[0] for row in rows.all()})
