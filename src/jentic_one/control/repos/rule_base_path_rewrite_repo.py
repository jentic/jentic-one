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
    agent_id: str | None = None  # binding rules only
    credential_id: str | None = None  # binding rules only


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
                RuleRow(
                    rule_id,
                    "binding",
                    f"{agent_id}:{credential_id}",
                    path,
                    mode or "regex",
                    agent_id=agent_id,
                    credential_id=credential_id,
                )
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
    async def credential_apis(session: AsyncSession) -> dict[str, ApiIdentity | None]:
        """Every credential's target API (``None`` = names no single API), in one read."""
        rows = await session.execute(
            select(
                Credential.id, Credential.api_vendor, Credential.api_name, Credential.api_version
            )
        )
        return {
            cid: ApiIdentity(vendor, name, version) if vendor and name and version else None
            for cid, vendor, name, version in rows.all()
        }

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


class BindingRow(NamedTuple):
    binding_id: str
    agent_id: str
    credential_id: str
    rule_set_id: str | None


_BINDINGS_SQL = text(
    "SELECT id, agent_id, credential_id, rule_set_id FROM agent_credential_bindings"
)


class RewriteRuleAdminRepository:
    """Admin-DB reads for the rewrite (raw SQL — no admin ORM import)."""

    @staticmethod
    async def bindings(session: AsyncSession) -> list[BindingRow]:
        rows = await session.execute(_BINDINGS_SQL)
        return [BindingRow(*row) for row in rows.all()]
