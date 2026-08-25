"""Repository for toolkit permission rule operations."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.toolkit_credential_bindings import ToolkitCredentialBinding
from jentic_one.control.core.schema.toolkit_permission_rules import ToolkitPermissionRule


class ToolkitPermissionRepository:
    """Data access layer for ToolkitPermissionRule entities — flush-only, never commits."""

    @staticmethod
    async def list_rules(
        session: AsyncSession,
        toolkit_id: str,
        credential_id: str,
        *,
        filters: Sequence[ColumnElement[bool]] | None = None,
    ) -> list[ToolkitPermissionRule]:
        """List rules ordered by sequence (user rules first, system rules last)."""
        stmt = (
            select(ToolkitPermissionRule)
            .where(
                ToolkitPermissionRule.toolkit_id == toolkit_id,
                ToolkitPermissionRule.credential_id == credential_id,
            )
            .order_by(
                ToolkitPermissionRule.is_system.asc(),
                ToolkitPermissionRule.sequence.asc(),
            )
        )
        if filters is not None:
            for f in filters:
                stmt = stmt.where(f)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def list_rules_for_vendor(
        session: AsyncSession,
        toolkit_id: str,
        api_vendor: str,
    ) -> list[ToolkitPermissionRule]:
        """List rules for the ``(toolkit, api_vendor)`` pool the broker evaluates.

        The broker's ``_RULES_QUERY`` joins ``toolkit_permission_rules``
        through ``toolkit_credential_bindings`` and ``credentials`` and
        filters by ``toolkit_id`` + ``api_vendor``, so rules attached to
        *any* binding of a same-vendor credential are pooled into one
        ordered list. A dry-run must evaluate this same pooled set — a
        naive per-binding read would misrepresent what the broker actually
        does (issue #751 review, dry-run parity).
        """
        stmt = (
            select(ToolkitPermissionRule)
            .join(
                ToolkitCredentialBinding,
                (ToolkitCredentialBinding.toolkit_id == ToolkitPermissionRule.toolkit_id)
                & (ToolkitCredentialBinding.credential_id == ToolkitPermissionRule.credential_id),
            )
            .join(Credential, Credential.id == ToolkitCredentialBinding.credential_id)
            .where(
                ToolkitPermissionRule.toolkit_id == toolkit_id,
                Credential.api_vendor == api_vendor,
            )
            .order_by(ToolkitPermissionRule.sequence.asc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def replace_user_rules(
        session: AsyncSession,
        toolkit_id: str,
        credential_id: str,
        rules: list[dict[str, object]],
        *,
        created_by: str,
    ) -> list[ToolkitPermissionRule]:
        """Delete non-system rows for the binding, insert new rows with sequential ordering."""
        await session.execute(
            delete(ToolkitPermissionRule).where(
                ToolkitPermissionRule.toolkit_id == toolkit_id,
                ToolkitPermissionRule.credential_id == credential_id,
                ToolkitPermissionRule.is_system.is_(False),
            )
        )
        new_rules: list[ToolkitPermissionRule] = []
        for idx, rule_data in enumerate(rules):
            rule = ToolkitPermissionRule(
                toolkit_id=toolkit_id,
                credential_id=credential_id,
                effect=str(rule_data.get("effect", "allow")),
                methods=rule_data.get("methods"),
                path=rule_data.get("path"),
                match_mode=str(rule_data.get("match_mode", "regex")),
                operations=rule_data.get("operations"),
                is_system=False,
                comment=rule_data.get("comment"),
                sequence=idx,
                created_by=created_by,
            )
            session.add(rule)
            new_rules.append(rule)
        await session.flush()
        return await ToolkitPermissionRepository.list_rules(session, toolkit_id, credential_id)

    @staticmethod
    async def patch_rules(
        session: AsyncSession,
        toolkit_id: str,
        credential_id: str,
        *,
        add: list[dict[str, object]] | None = None,
        remove: list[int] | None = None,
        created_by: str,
    ) -> list[ToolkitPermissionRule]:
        """Add/remove user rules per PermissionsPatchRequest semantics."""
        existing = await ToolkitPermissionRepository.list_rules(session, toolkit_id, credential_id)
        user_rules = [r for r in existing if not r.is_system]

        if remove:
            remove_set = set(remove)
            to_delete = [r for i, r in enumerate(user_rules) if i in remove_set]
            for rule in to_delete:
                await session.delete(rule)
            user_rules = [r for i, r in enumerate(user_rules) if i not in remove_set]

        max_seq = max((r.sequence for r in user_rules), default=-1)
        if add:
            for idx, rule_data in enumerate(add):
                rule = ToolkitPermissionRule(
                    toolkit_id=toolkit_id,
                    credential_id=credential_id,
                    effect=str(rule_data.get("effect", "allow")),
                    methods=rule_data.get("methods"),
                    path=rule_data.get("path"),
                    match_mode=str(rule_data.get("match_mode", "regex")),
                    operations=rule_data.get("operations"),
                    is_system=False,
                    comment=rule_data.get("comment"),
                    sequence=max_seq + 1 + idx,
                    created_by=created_by,
                )
                session.add(rule)

        await session.flush()
        return await ToolkitPermissionRepository.list_rules(session, toolkit_id, credential_id)
