"""ToolkitService — orchestrates toolkit CRUD operations within transactions."""

from __future__ import annotations

from typing import NoReturn

import structlog

from jentic_one.control.core.schema.toolkit_credential_bindings import ToolkitCredentialBinding
from jentic_one.control.core.schema.toolkit_keys import ToolkitKey
from jentic_one.control.core.schema.toolkit_permission_rules import ToolkitPermissionRule
from jentic_one.control.core.schema.toolkits import Toolkit
from jentic_one.control.repos import (
    CredentialRepository,
    ToolkitBindingRepository,
    ToolkitKeyRepository,
    ToolkitPermissionRepository,
    ToolkitRepository,
)
from jentic_one.control.repos.prerequisite_repo import BoundAgentRow, PrerequisiteRepository
from jentic_one.control.scoping.filters import build_access_filters
from jentic_one.control.services.toolkits.errors import (
    BindingNotFoundError,
    ConflictingApiBindingError,
    DuplicateBindingError,
    ToolkitAccessDeniedError,
    ToolkitKeyNotFoundError,
    ToolkitNotFoundError,
)
from jentic_one.control.services.toolkits.key_gen import generate_toolkit_key
from jentic_one.control.services.toolkits.schemas import BindingPage, BindingWithPermissions
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit_best_effort
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.events import emit_event_best_effort
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.pagination import decode_cursor_str, encode_cursor
from jentic_one.shared.scopes import ORG_ADMIN

logger = structlog.get_logger()


class ToolkitService:
    """Style A standalone service for toolkit CRUD operations."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def _bound_toolkit_ids(self, identity: Identity) -> list[str]:
        """Toolkit ids the caller is bound to, widening owner-scoped visibility.

        A caller must always be able to read a toolkit it is actively bound to,
        even one it doesn't own — including an orphaned agent that owns nothing
        (issues #665/#682). Bindings live in the admin DB, so resolve the ids
        there and feed them into the control-DB ``build_access_filters``. An
        ``org:admin`` caller is unrestricted already, so skip the lookup.
        """
        if ORG_ADMIN in identity.permissions or not identity.sub:
            return []
        async with self._ctx.admin_db.session() as session:
            return await PrerequisiteRepository.list_toolkit_ids_for_agent(
                session, agent_id=identity.sub
            )

    async def _raise_toolkit_unavailable(self, toolkit_id: str) -> NoReturn:
        """Raise the right error when a scoped toolkit lookup came back empty.

        A write handler that resolved the toolkit under access filters and found
        nothing can mean one of two things: the toolkit id is genuinely unknown
        (``404``), or it exists but this caller isn't allowed to see it — not the
        owner, not bound, not ``org:admin`` (``403``). An unscoped existence probe
        tells the two apart so we stop reporting an authorization outcome as a
        misleading ``404 toolkit_not_found`` (issue #682).
        """
        async with self._ctx.control_db.session() as session:
            exists = await ToolkitRepository.get_by_id(session, toolkit_id)
        if exists is not None:
            raise ToolkitAccessDeniedError(toolkit_id)
        raise ToolkitNotFoundError(toolkit_id)

    async def _emit_telemetry(self, *, type: str, summary: str, identity: Identity) -> None:
        """Emit a telemetry event on the admin DB (best-effort).

        Toolkit writes land in the control DB; telemetry lives in the admin DB,
        so we open a separate short admin transaction next to the audit write.
        """
        try:
            async with self._ctx.admin_db.transaction() as session:
                await emit_event_best_effort(
                    session,
                    type=type,
                    severity=EventSeverity.INFO,
                    summary=summary,
                    created_by=identity.sub,
                    actor_id=identity.sub,
                    actor_type=identity.actor_type.value,
                )
        except Exception:
            logger.warning("telemetry_emit_failed", event_type=type, exc_info=True)

    async def create(
        self,
        *,
        name: str,
        identity: Identity,
        description: str | None = None,
        active: bool = True,
        permissions: list[dict[str, object]] | None = None,
        credential_ids: list[str] | None = None,
    ) -> tuple[Toolkit, str]:
        """Create a toolkit with an initial API key.

        Returns (toolkit, plaintext_key).
        """
        plaintext, hashed, preview, lookup = generate_toolkit_key()

        async with self._ctx.control_db.transaction() as session:
            toolkit = await ToolkitRepository.create(
                session,
                name=name,
                description=description,
                active=active,
                permissions=permissions,
                created_by=identity.sub,
            )
            await ToolkitKeyRepository.create(
                session,
                toolkit_id=toolkit.id,
                hashed_key=hashed,
                key_preview=preview,
                lookup_hash=lookup,
                created_by=identity.sub,
            )
            if credential_ids:
                for cred_id in credential_ids:
                    await ToolkitBindingRepository.bind(
                        session,
                        toolkit_id=toolkit.id,
                        credential_id=cred_id,
                        created_by=identity.sub,
                    )
            loaded = await ToolkitRepository.get_with_relations(session, toolkit.id)
            assert loaded is not None
            toolkit = loaded

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.CREATE,
            target_type=AuditTargetType.TOOLKIT,
            target_id=toolkit.id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            after={"name": name, "active": active},
            origin=identity.origin.value,
        )
        await self._emit_telemetry(
            type=EventType.TOOLKIT_CREATED,
            summary=f"Toolkit {toolkit.id} created",
            identity=identity,
        )
        return toolkit, plaintext

    async def get(self, toolkit_id: str, *, identity: Identity) -> Toolkit:
        bound_ids = await self._bound_toolkit_ids(identity)
        access_filters = build_access_filters(identity, Toolkit, bound_toolkit_ids=bound_ids)
        async with self._ctx.control_db.session() as session:
            toolkit = await ToolkitRepository.get_with_relations(
                session, toolkit_id, filters=access_filters
            )
            if toolkit is None:
                raise ToolkitNotFoundError(toolkit_id)
            return toolkit

    async def list_all(
        self, *, cursor: str | None = None, limit: int = 50, identity: Identity
    ) -> tuple[list[Toolkit], bool, str | None]:
        """List toolkits. Returns (data, has_more, next_cursor)."""
        decoded_cursor = None
        if cursor is not None:
            ts, cid = decode_cursor_str(cursor)
            decoded_cursor = (ts, cid)

        access_filters = build_access_filters(
            identity, Toolkit, bound_toolkit_ids=await self._bound_toolkit_ids(identity)
        )
        async with self._ctx.control_db.session() as session:
            rows = await ToolkitRepository.list_all(
                session, cursor=decoded_cursor, limit=limit, filters=access_filters
            )

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor(last.created_at, last.id)

        return rows, has_more, next_cursor

    async def list_agents(
        self,
        toolkit_id: str,
        *,
        cursor: str | None = None,
        limit: int = 50,
        identity: Identity,
    ) -> tuple[list[BoundAgentRow], bool, str | None]:
        """List agents bound to a toolkit. Returns (data, has_more, next_cursor)."""
        access_filters = build_access_filters(
            identity, Toolkit, bound_toolkit_ids=await self._bound_toolkit_ids(identity)
        )
        async with self._ctx.control_db.session() as session:
            toolkit = await ToolkitRepository.get_by_id(session, toolkit_id, filters=access_filters)
            if toolkit is None:
                raise ToolkitNotFoundError(toolkit_id)

        decoded_cursor = None
        if cursor is not None:
            ts, cid = decode_cursor_str(cursor)
            decoded_cursor = (ts, cid)

        async with self._ctx.admin_db.session() as session:
            rows = await PrerequisiteRepository.list_agents_for_toolkit(
                session, toolkit_id=toolkit_id, cursor=decoded_cursor, limit=limit + 1
            )

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor(last.bound_at, last.binding_id)

        return rows, has_more, next_cursor

    async def update(
        self,
        toolkit_id: str,
        *,
        identity: Identity,
        name: str | None = None,
        description: str | None = None,
        active: bool | None = None,
    ) -> Toolkit:
        access_filters = build_access_filters(
            identity, Toolkit, bound_toolkit_ids=await self._bound_toolkit_ids(identity)
        )
        async with self._ctx.control_db.transaction() as session:
            existing = await ToolkitRepository.get_by_id(
                session, toolkit_id, filters=access_filters
            )
            if existing is None:
                await self._raise_toolkit_unavailable(toolkit_id)
            before_name = existing.name
            before_active = existing.active
            toolkit = await ToolkitRepository.update(
                session, toolkit_id, name=name, description=description, active=active
            )
            if toolkit is None:
                raise ToolkitNotFoundError(toolkit_id)

        action = AuditAction.UPDATE
        if active is not None and before_active != active:
            action = AuditAction.ENABLE if active else AuditAction.DISABLE
        await record_audit_best_effort(
            self._ctx,
            action=action,
            target_type=AuditTargetType.TOOLKIT,
            target_id=toolkit_id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            before={"name": before_name, "active": before_active},
            after={"name": toolkit.name, "active": toolkit.active},
            origin=identity.origin.value,
        )
        return toolkit

    async def delete(self, toolkit_id: str, *, identity: Identity) -> None:
        access_filters = build_access_filters(
            identity, Toolkit, bound_toolkit_ids=await self._bound_toolkit_ids(identity)
        )
        async with self._ctx.control_db.transaction() as session:
            toolkit = await ToolkitRepository.get_by_id(session, toolkit_id, filters=access_filters)
            if toolkit is None:
                await self._raise_toolkit_unavailable(toolkit_id)
            await ToolkitRepository.delete(session, toolkit_id)

        async with self._ctx.admin_db.transaction() as session:
            await PrerequisiteRepository.delete_agent_toolkit_bindings_for_toolkit(
                session, toolkit_id=toolkit_id
            )

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.DELETE,
            target_type=AuditTargetType.TOOLKIT,
            target_id=toolkit_id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            origin=identity.origin.value,
        )

    # --- Key management ---

    async def create_key(
        self,
        toolkit_id: str,
        *,
        identity: Identity,
        label: str | None = None,
        allowed_ips: list[str] | None = None,
    ) -> tuple[ToolkitKey, str]:
        """Create an additional API key. Returns (key, plaintext)."""
        plaintext, hashed, preview, lookup = generate_toolkit_key()
        access_filters = build_access_filters(
            identity, Toolkit, bound_toolkit_ids=await self._bound_toolkit_ids(identity)
        )

        async with self._ctx.control_db.transaction() as session:
            toolkit = await ToolkitRepository.get_by_id(session, toolkit_id, filters=access_filters)
            if toolkit is None:
                await self._raise_toolkit_unavailable(toolkit_id)
            key = await ToolkitKeyRepository.create(
                session,
                toolkit_id=toolkit_id,
                hashed_key=hashed,
                key_preview=preview,
                lookup_hash=lookup,
                label=label,
                allowed_ips=allowed_ips,
                created_by=identity.sub,
            )
        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.CREATE,
            target_type=AuditTargetType.TOOLKIT_KEY,
            target_id=key.id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            target_parent_id=toolkit_id,
            origin=identity.origin.value,
        )
        await self._emit_telemetry(
            type=EventType.TOOLKIT_KEY_CREATED,
            summary=f"Toolkit key {key.id} created",
            identity=identity,
        )
        return key, plaintext

    async def list_keys(
        self, toolkit_id: str, *, cursor: str | None = None, limit: int = 50, identity: Identity
    ) -> tuple[list[ToolkitKey], bool, str | None]:
        """List keys for a toolkit. Returns (data, has_more, next_cursor)."""
        decoded_cursor = None
        if cursor is not None:
            ts, cid = decode_cursor_str(cursor)
            decoded_cursor = (ts, cid)

        async with self._ctx.control_db.session() as session:
            toolkit = await ToolkitRepository.get_by_id(
                session,
                toolkit_id,
                filters=build_access_filters(
                    identity, Toolkit, bound_toolkit_ids=await self._bound_toolkit_ids(identity)
                ),
            )
            if toolkit is None:
                raise ToolkitNotFoundError(toolkit_id)
            rows = await ToolkitKeyRepository.list_by_toolkit(
                session, toolkit_id, cursor=decoded_cursor, limit=limit
            )

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor(last.created_at, last.id)

        return rows, has_more, next_cursor

    async def update_key(
        self,
        toolkit_id: str,
        key_id: str,
        *,
        identity: Identity,
        label: str | None = None,
        allowed_ips: list[str] | None = None,
        revoked: bool | None = None,
    ) -> ToolkitKey:
        access_filters = build_access_filters(
            identity, Toolkit, bound_toolkit_ids=await self._bound_toolkit_ids(identity)
        )
        async with self._ctx.control_db.transaction() as session:
            toolkit = await ToolkitRepository.get_by_id(session, toolkit_id, filters=access_filters)
            if toolkit is None:
                await self._raise_toolkit_unavailable(toolkit_id)
            key = await ToolkitKeyRepository.get_by_id(session, key_id)
            if key is None or key.toolkit_id != toolkit_id:
                raise ToolkitKeyNotFoundError(key_id)
            updated = await ToolkitKeyRepository.update(
                session, key_id, label=label, allowed_ips=allowed_ips, revoked=revoked
            )
            assert updated is not None

        action = AuditAction.UPDATE
        if revoked is not None and key.revoked != revoked:
            action = AuditAction.REVOKE if revoked else AuditAction.ENABLE
        await record_audit_best_effort(
            self._ctx,
            action=action,
            target_type=AuditTargetType.TOOLKIT_KEY,
            target_id=key_id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            target_parent_id=toolkit_id,
            origin=identity.origin.value,
        )
        return updated

    async def delete_key(self, toolkit_id: str, key_id: str, *, identity: Identity) -> None:
        access_filters = build_access_filters(
            identity, Toolkit, bound_toolkit_ids=await self._bound_toolkit_ids(identity)
        )
        async with self._ctx.control_db.transaction() as session:
            toolkit = await ToolkitRepository.get_by_id(session, toolkit_id, filters=access_filters)
            if toolkit is None:
                await self._raise_toolkit_unavailable(toolkit_id)
            key = await ToolkitKeyRepository.get_by_id(session, key_id)
            if key is None or key.toolkit_id != toolkit_id:
                raise ToolkitKeyNotFoundError(key_id)
            await ToolkitKeyRepository.delete(session, key_id)

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.DELETE,
            target_type=AuditTargetType.TOOLKIT_KEY,
            target_id=key_id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            target_parent_id=toolkit_id,
            origin=identity.origin.value,
        )

    # --- Credential binding ---

    async def bind_credential(
        self,
        toolkit_id: str,
        credential_id: str,
        *,
        identity: Identity,
        permissions: list[dict[str, object]] | None = None,
    ) -> ToolkitCredentialBinding:
        access_filters = build_access_filters(
            identity, Toolkit, bound_toolkit_ids=await self._bound_toolkit_ids(identity)
        )
        async with self._ctx.control_db.transaction() as session:
            toolkit = await ToolkitRepository.get_by_id(session, toolkit_id, filters=access_filters)
            if toolkit is None:
                await self._raise_toolkit_unavailable(toolkit_id)
            existing = await ToolkitBindingRepository.get(session, toolkit_id, credential_id)
            if existing is not None:
                raise DuplicateBindingError(toolkit_id, credential_id)
            credential = await CredentialRepository.get_by_id(session, credential_id)
            if credential is not None:
                conflicts = await ToolkitBindingRepository.list_active_bound_credentials_for_api(
                    session,
                    toolkit_id=toolkit_id,
                    api_vendor=credential.api_vendor,
                    api_name=credential.api_name,
                    api_version=credential.api_version,
                    exclude_credential_id=credential_id,
                )
                if conflicts:
                    raise ConflictingApiBindingError(
                        toolkit_id,
                        credential_id,
                        conflicts[0].id,
                        credential.api_vendor,
                        credential.api_name,
                        credential.api_version,
                    )
            binding = await ToolkitBindingRepository.bind(
                session, toolkit_id=toolkit_id, credential_id=credential_id, created_by=identity.sub
            )
            if permissions:
                await ToolkitPermissionRepository.replace_user_rules(
                    session, toolkit_id, credential_id, permissions, created_by=identity.sub
                )
        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.GRANT,
            target_type=AuditTargetType.CREDENTIAL_BINDING,
            target_id=credential_id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            target_parent_id=toolkit_id,
            reason="bind credential to toolkit",
            origin=identity.origin.value,
        )
        await self._emit_telemetry(
            type=EventType.CREDENTIAL_BOUND_TO_TOOLKIT,
            summary=f"Credential {credential_id} bound to toolkit {toolkit_id}",
            identity=identity,
        )
        return binding

    async def list_bindings(
        self, toolkit_id: str, *, cursor: str | None = None, limit: int = 50, identity: Identity
    ) -> BindingPage:
        """List credential bindings with their permission rules."""
        decoded_cursor = None
        if cursor is not None:
            ts, cid = decode_cursor_str(cursor)
            decoded_cursor = (ts, cid)

        async with self._ctx.control_db.session() as session:
            toolkit = await ToolkitRepository.get_by_id(
                session,
                toolkit_id,
                filters=build_access_filters(
                    identity, Toolkit, bound_toolkit_ids=await self._bound_toolkit_ids(identity)
                ),
            )
            if toolkit is None:
                raise ToolkitNotFoundError(toolkit_id)
            rows = await ToolkitBindingRepository.list_by_toolkit(
                session, toolkit_id, cursor=decoded_cursor, limit=limit
            )

            has_more = len(rows) > limit
            if has_more:
                rows = rows[:limit]

            items: list[BindingWithPermissions] = []
            for b in rows:
                rules = await ToolkitPermissionRepository.list_rules(
                    session, b.toolkit_id, b.credential_id
                )
                items.append(BindingWithPermissions(binding=b, rules=rules))

        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor(last.bound_at, last.credential_id)

        return BindingPage(data=items, has_more=has_more, next_cursor=next_cursor)

    async def unbind_credential(
        self, toolkit_id: str, credential_id: str, *, identity: Identity
    ) -> None:
        access_filters = build_access_filters(
            identity, Toolkit, bound_toolkit_ids=await self._bound_toolkit_ids(identity)
        )
        async with self._ctx.control_db.transaction() as session:
            toolkit = await ToolkitRepository.get_by_id(session, toolkit_id, filters=access_filters)
            if toolkit is None:
                await self._raise_toolkit_unavailable(toolkit_id)
            deleted = await ToolkitBindingRepository.unbind(session, toolkit_id, credential_id)
            if not deleted:
                raise BindingNotFoundError(toolkit_id, credential_id)

        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.REVOKE,
            target_type=AuditTargetType.CREDENTIAL_BINDING,
            target_id=credential_id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            target_parent_id=toolkit_id,
            reason="unbind credential from toolkit",
            origin=identity.origin.value,
        )
        await self._emit_telemetry(
            type=EventType.CREDENTIAL_UNBOUND_FROM_TOOLKIT,
            summary=f"Credential {credential_id} unbound from toolkit {toolkit_id}",
            identity=identity,
        )

    # --- Permission rules ---

    async def list_permissions(
        self, toolkit_id: str, credential_id: str, *, identity: Identity
    ) -> list[ToolkitPermissionRule]:
        async with self._ctx.control_db.session() as session:
            toolkit = await ToolkitRepository.get_by_id(
                session,
                toolkit_id,
                filters=build_access_filters(
                    identity, Toolkit, bound_toolkit_ids=await self._bound_toolkit_ids(identity)
                ),
            )
            if toolkit is None:
                raise ToolkitNotFoundError(toolkit_id)
            binding = await ToolkitBindingRepository.get(session, toolkit_id, credential_id)
            if binding is None:
                raise BindingNotFoundError(toolkit_id, credential_id)
            return await ToolkitPermissionRepository.list_rules(session, toolkit_id, credential_id)

    async def replace_permissions(
        self,
        toolkit_id: str,
        credential_id: str,
        rules: list[dict[str, object]],
        *,
        identity: Identity,
    ) -> list[ToolkitPermissionRule]:
        access_filters = build_access_filters(
            identity, Toolkit, bound_toolkit_ids=await self._bound_toolkit_ids(identity)
        )
        async with self._ctx.control_db.transaction() as session:
            toolkit = await ToolkitRepository.get_by_id(session, toolkit_id, filters=access_filters)
            if toolkit is None:
                await self._raise_toolkit_unavailable(toolkit_id)
            binding = await ToolkitBindingRepository.get(session, toolkit_id, credential_id)
            if binding is None:
                raise BindingNotFoundError(toolkit_id, credential_id)
            result = await ToolkitPermissionRepository.replace_user_rules(
                session, toolkit_id, credential_id, rules, created_by=identity.sub
            )
        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.UPDATE,
            target_type=AuditTargetType.CREDENTIAL_BINDING,
            target_id=credential_id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            target_parent_id=toolkit_id,
            reason="replace permission rules",
            origin=identity.origin.value,
        )
        await self._emit_telemetry(
            type=EventType.TOOLKIT_PERMISSION_RULE_SET,
            summary=f"Permission rules set on toolkit {toolkit_id}",
            identity=identity,
        )
        return result

    async def patch_permissions(
        self,
        toolkit_id: str,
        credential_id: str,
        *,
        identity: Identity,
        add: list[dict[str, object]] | None = None,
        remove: list[int] | None = None,
    ) -> list[ToolkitPermissionRule]:
        access_filters = build_access_filters(
            identity, Toolkit, bound_toolkit_ids=await self._bound_toolkit_ids(identity)
        )
        async with self._ctx.control_db.transaction() as session:
            toolkit = await ToolkitRepository.get_by_id(session, toolkit_id, filters=access_filters)
            if toolkit is None:
                await self._raise_toolkit_unavailable(toolkit_id)
            binding = await ToolkitBindingRepository.get(session, toolkit_id, credential_id)
            if binding is None:
                raise BindingNotFoundError(toolkit_id, credential_id)
            result = await ToolkitPermissionRepository.patch_rules(
                session, toolkit_id, credential_id, add=add, remove=remove, created_by=identity.sub
            )
        await record_audit_best_effort(
            self._ctx,
            action=AuditAction.UPDATE,
            target_type=AuditTargetType.CREDENTIAL_BINDING,
            target_id=credential_id,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            target_parent_id=toolkit_id,
            reason="patch permission rules",
            origin=identity.origin.value,
        )
        await self._emit_telemetry(
            type=EventType.TOOLKIT_PERMISSION_RULE_SET,
            summary=f"Permission rules patched on toolkit {toolkit_id}",
            identity=identity,
        )
        return result
