"""ServiceAccount lifecycle management service."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.service_accounts import ServiceAccount
from jentic_one.admin.repos import (
    ActorScopeGrantRepository,
    ServiceAccountRepository,
)
from jentic_one.admin.scoping.filters import build_access_filters
from jentic_one.auth.services.errors import (
    ActorNotFoundError,
    InvalidTransitionError,
    ServiceAccountMigratedError,
)
from jentic_one.auth.services.schemas.service_accounts import (
    ServiceAccountCreatePayload,
    ServiceAccountView,
)
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus, ActorType, ActorVerb
from jentic_one.shared.pagination import Page, decode_cursor_str, encode_cursor

_VALID_TRANSITIONS: dict[ActorVerb, dict[ActorStatus, ActorStatus]] = {
    ActorVerb.APPROVE: {ActorStatus.PENDING: ActorStatus.ACTIVE},
    ActorVerb.DENY: {ActorStatus.PENDING: ActorStatus.REJECTED},
    ActorVerb.DISABLE: {ActorStatus.ACTIVE: ActorStatus.DISABLED},
    ActorVerb.ENABLE: {ActorStatus.DISABLED: ActorStatus.ACTIVE},
}


class ServiceAccountService:
    """Manages service account lifecycle."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def create(
        self, payload: ServiceAccountCreatePayload, *, owner_id: str, identity: Identity
    ) -> ServiceAccountView:
        async with self._ctx.admin_db.transaction() as session:
            sa = await ServiceAccountRepository.create(
                session,
                name=payload.name,
                owner_id=owner_id,
                registered_by=identity.sub,
                description=payload.description,
                created_by=identity.sub,
            )
            await record_audit(
                session,
                action=AuditAction.REGISTER,
                target_type=AuditTargetType.SERVICE_ACCOUNT,
                target_id=sa.id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                after={"name": payload.name, "owner_id": owner_id},
                origin=identity.origin.value,
            )
            sa = await ServiceAccountRepository.set_approval(
                session, sa.id, approved_by=identity.sub
            )
            if payload.scopes:
                for scope in list(dict.fromkeys(payload.scopes)):
                    await ActorScopeGrantRepository.grant(
                        session,
                        actor_id=sa.id,
                        actor_type=ActorType.SERVICE_ACCOUNT,
                        scope=scope,
                        granted_by=identity.sub,
                        created_by=identity.sub,
                    )
            await record_audit(
                session,
                action=AuditAction.APPROVE,
                target_type=AuditTargetType.SERVICE_ACCOUNT,
                target_id=sa.id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                origin=identity.origin.value,
            )
        return ServiceAccountView.model_validate(sa)

    async def list_service_accounts(
        self,
        *,
        owner_id: str | None = None,
        limit: int = 50,
        status: str | None = None,
        cursor: str | None = None,
        identity: Identity,
    ) -> Page[ServiceAccountView]:
        cursor_dt = None
        if cursor is not None:
            cursor_dt, _ = decode_cursor_str(cursor)

        access_filters = build_access_filters(identity, ServiceAccount)

        async with self._ctx.admin_db.session() as session:
            if owner_id is not None:
                accounts = await ServiceAccountRepository.list_by_owner(
                    session, owner_id, limit=limit + 1, cursor=cursor_dt, filters=access_filters
                )
            else:
                accounts = await ServiceAccountRepository.list_all(
                    session,
                    limit=limit + 1,
                    status=status,
                    cursor=cursor_dt,
                    filters=access_filters,
                )

        has_more = len(accounts) > limit
        if has_more:
            accounts = accounts[:limit]

        views = [ServiceAccountView.model_validate(a) for a in accounts]

        next_cursor = None
        if has_more and accounts:
            next_cursor = encode_cursor(accounts[-1].created_at, accounts[-1].id)

        return Page(data=views, has_more=has_more, next_cursor=next_cursor)

    async def get_service_account(
        self, service_account_id: str, *, identity: Identity
    ) -> ServiceAccountView:
        access_filters = build_access_filters(identity, ServiceAccount)
        async with self._ctx.admin_db.session() as session:
            sa = await ServiceAccountRepository.get_by_id(
                session, service_account_id, filters=access_filters
            )
        if sa is None:
            raise ActorNotFoundError(service_account_id)
        return ServiceAccountView.model_validate(sa)

    async def approve(self, service_account_id: str, *, identity: Identity) -> ServiceAccountView:
        async with self._ctx.admin_db.transaction() as session:
            await self._check_transition(session, service_account_id, ActorVerb.APPROVE)
            sa = await ServiceAccountRepository.set_approval(
                session, service_account_id, approved_by=identity.sub
            )
            await record_audit(
                session,
                action=AuditAction.APPROVE,
                target_type=AuditTargetType.SERVICE_ACCOUNT,
                target_id=service_account_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                origin=identity.origin.value,
            )
        return ServiceAccountView.model_validate(sa)

    async def deny(
        self, service_account_id: str, *, reason: str, identity: Identity
    ) -> ServiceAccountView:
        async with self._ctx.admin_db.transaction() as session:
            await self._check_transition(session, service_account_id, ActorVerb.DENY)
            sa = await ServiceAccountRepository.set_denial(
                session, service_account_id, reason=reason, denied_by=identity.sub
            )
            await record_audit(
                session,
                action=AuditAction.DENY,
                target_type=AuditTargetType.SERVICE_ACCOUNT,
                target_id=service_account_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                reason=reason,
                origin=identity.origin.value,
            )
        return ServiceAccountView.model_validate(sa)

    async def disable(self, service_account_id: str, *, identity: Identity) -> None:
        async with self._ctx.admin_db.transaction() as session:
            await self._check_transition(session, service_account_id, ActorVerb.DISABLE)
            await ServiceAccountRepository.update_status(
                session, service_account_id, ActorStatus.DISABLED
            )
            await record_audit(
                session,
                action=AuditAction.DISABLE,
                target_type=AuditTargetType.SERVICE_ACCOUNT,
                target_id=service_account_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                origin=identity.origin.value,
            )

    async def enable(self, service_account_id: str, *, identity: Identity) -> None:
        async with self._ctx.admin_db.transaction() as session:
            await self._check_transition(session, service_account_id, ActorVerb.ENABLE)
            await ServiceAccountRepository.update_status(
                session, service_account_id, ActorStatus.ACTIVE
            )
            await record_audit(
                session,
                action=AuditAction.ENABLE,
                target_type=AuditTargetType.SERVICE_ACCOUNT,
                target_id=service_account_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                origin=identity.origin.value,
            )

    async def archive(self, service_account_id: str, *, identity: Identity) -> None:
        async with self._ctx.admin_db.transaction() as session:
            sa = await ServiceAccountRepository.get_by_id(session, service_account_id)
            if sa is None:
                raise ActorNotFoundError(service_account_id)
            _refuse_if_migrated(sa)
            if sa.status == ActorStatus.ARCHIVED:
                raise InvalidTransitionError(service_account_id, ActorStatus.ARCHIVED, "archive")
            await ServiceAccountRepository.archive(session, service_account_id)
            await ActorScopeGrantRepository.revoke_all(session, service_account_id)
            await record_audit(
                session,
                action=AuditAction.ARCHIVE,
                target_type=AuditTargetType.SERVICE_ACCOUNT,
                target_id=service_account_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                origin=identity.origin.value,
            )

    async def get_scopes(self, service_account_id: str, *, identity: Identity) -> list[str]:
        await self.get_service_account(service_account_id, identity=identity)
        async with self._ctx.admin_db.session() as session:
            grants = await ActorScopeGrantRepository.list_for_actor(
                session, service_account_id, actor_type=ActorType.SERVICE_ACCOUNT
            )
        return [g.scope for g in grants]

    async def replace_scopes(
        self, service_account_id: str, scopes: list[str], *, identity: Identity
    ) -> list[str]:
        async with self._ctx.admin_db.transaction() as session:
            sa = await ServiceAccountRepository.get_by_id(session, service_account_id)
            if sa is None:
                raise ActorNotFoundError(service_account_id)
            _refuse_if_migrated(sa)
            if sa.status == ActorStatus.ARCHIVED:
                raise InvalidTransitionError(
                    service_account_id, ActorStatus.ARCHIVED, "replace_scopes"
                )
            await ActorScopeGrantRepository.revoke_all(session, service_account_id)
            scopes = list(dict.fromkeys(scopes))
            for scope in scopes:
                await ActorScopeGrantRepository.grant(
                    session,
                    actor_id=service_account_id,
                    actor_type=ActorType.SERVICE_ACCOUNT,
                    scope=scope,
                    granted_by=identity.sub,
                    created_by=identity.sub,
                )
            await record_audit(
                session,
                action=AuditAction.GRANT,
                target_type=AuditTargetType.SERVICE_ACCOUNT,
                target_id=service_account_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                after={"scopes": scopes},
                reason="replace_scopes",
                origin=identity.origin.value,
            )
        return scopes

    async def _check_transition(
        self, session: AsyncSession, service_account_id: str, verb: ActorVerb
    ) -> None:
        sa = await ServiceAccountRepository.get_by_id(session, service_account_id)
        if sa is None:
            raise ActorNotFoundError(service_account_id)
        _refuse_if_migrated(sa)
        if sa.status == ActorStatus.ARCHIVED:
            raise InvalidTransitionError(service_account_id, ActorStatus.ARCHIVED, verb)
        allowed_from = _VALID_TRANSITIONS[verb]
        if sa.status not in allowed_from:
            raise InvalidTransitionError(service_account_id, sa.status, verb)


def _refuse_if_migrated(sa: ServiceAccount) -> None:
    """Theme-8 Phase 1 stamp guard (NF-1/NF-2): refuse mutations on stamped rows.

    Uniform predicate ``migrated_to_actor_id IS NOT NULL`` — the live actor
    is the successor agent (or, for skip-but-stamp rows, nothing at all), so
    a mutation here would silently no-op against the key or diverge the
    copied state. Runbook: dual-kill — disable the successor agent.
    Reads stay unguarded; ``create`` too (new rows are unstamped, the boot
    job re-runs migrate them, F5).
    """
    if sa.migrated_to_actor_id is not None:
        successor = sa.migrated_to_actor_id
        raise ServiceAccountMigratedError(sa.id, None if successor == "skipped" else successor)
