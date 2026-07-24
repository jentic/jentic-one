"""Agent lifecycle management service."""

from __future__ import annotations

from sqlalchemy import bindparam, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.repos import (
    ActorScopeGrantRepository,
    AgentCredentialRepository,
    AgentRepository,
    AgentToolkitBindingRepository,
)
from jentic_one.admin.scoping.filters import build_access_filters
from jentic_one.auth.repos import ToolkitNameRepository
from jentic_one.auth.services.errors import (
    ActorNotFoundError,
    InvalidOwnerError,
    InvalidTransitionError,
    ToolkitBindingConflictError,
    ToolkitBindingNotFoundError,
)
from jentic_one.auth.services.schemas.agents import (
    AgentCreatePayload,
    AgentView,
    ServedApi,
    ToolkitBindingView,
)
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db import DatabaseIntegrityError
from jentic_one.shared.events import emit_event_best_effort
from jentic_one.shared.models import ActorStatus, ActorType, ActorVerb
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.pagination import Page, decode_cursor_str, encode_cursor
from jentic_one.shared.scopes import DEFAULT_AGENT_SCOPES

_VALID_TRANSITIONS: dict[ActorVerb, dict[ActorStatus, ActorStatus]] = {
    ActorVerb.APPROVE: {ActorStatus.PENDING: ActorStatus.ACTIVE},
    ActorVerb.DENY: {ActorStatus.PENDING: ActorStatus.REJECTED},
    ActorVerb.DISABLE: {ActorStatus.ACTIVE: ActorStatus.DISABLED},
    ActorVerb.ENABLE: {ActorStatus.DISABLED: ActorStatus.ACTIVE},
}


class AgentService:
    """Manages agent lifecycle: create, list, get, approve, deny, disable, enable, archive."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def create(
        self, payload: AgentCreatePayload, *, owner_id: str, identity: Identity
    ) -> AgentView:
        async with self._ctx.admin_db.transaction() as session:
            agent = await AgentRepository.create(
                session,
                name=payload.name,
                owner_id=owner_id,
                registered_by=identity.sub,
                description=payload.description,
                created_by=identity.sub,
                status=ActorStatus.ACTIVE,
            )
            scopes_to_grant = (
                list(dict.fromkeys(payload.scopes))
                if payload.scopes
                else list(DEFAULT_AGENT_SCOPES)
            )
            for scope in scopes_to_grant:
                await ActorScopeGrantRepository.grant(
                    session,
                    actor_id=agent.id,
                    actor_type=ActorType.AGENT,
                    scope=scope,
                    granted_by=identity.sub,
                    created_by=identity.sub,
                )
            await record_audit(
                session,
                action=AuditAction.REGISTER,
                target_type=AuditTargetType.AGENT,
                target_id=agent.id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                after={"name": payload.name, "owner_id": owner_id},
                origin=identity.origin.value,
            )
            await emit_event_best_effort(
                session,
                type=EventType.AGENT_CREATED,
                severity=EventSeverity.INFO,
                summary=f"Agent {agent.id} created",
                created_by=identity.sub,
                actor_id=identity.sub,
                actor_type=identity.actor_type.value,
            )
        return AgentView.model_validate(agent)

    async def list_agents(
        self,
        *,
        owner_id: str | None = None,
        limit: int = 50,
        status: str | None = None,
        cursor: str | None = None,
        identity: Identity,
    ) -> Page[AgentView]:
        cursor_dt = None
        if cursor is not None:
            cursor_dt, _ = decode_cursor_str(cursor)

        access_filters = build_access_filters(identity, Agent)

        async with self._ctx.admin_db.session() as session:
            if owner_id is not None:
                agents = await AgentRepository.list_by_owner(
                    session, owner_id, limit=limit + 1, cursor=cursor_dt, filters=access_filters
                )
            else:
                agents = await AgentRepository.list_all(
                    session,
                    limit=limit + 1,
                    status=status,
                    cursor=cursor_dt,
                    filters=access_filters,
                )

        has_more = len(agents) > limit
        if has_more:
            agents = agents[:limit]

        views = [AgentView.model_validate(a) for a in agents]

        next_cursor = None
        if has_more and agents:
            next_cursor = encode_cursor(agents[-1].created_at, agents[-1].id)

        return Page(data=views, has_more=has_more, next_cursor=next_cursor)

    async def get_agent(self, agent_id: str, *, identity: Identity) -> AgentView:
        access_filters = build_access_filters(identity, Agent)
        async with self._ctx.admin_db.session() as session:
            agent = await AgentRepository.get_by_id(session, agent_id, filters=access_filters)
            if agent is None:
                raise ActorNotFoundError(agent_id)
            has_key = await AgentCredentialRepository.has_api_key(session, agent_id)
        view = AgentView.model_validate(agent)
        view.has_api_key = has_key
        return view

    async def approve(self, agent_id: str, *, identity: Identity) -> AgentView:
        async with self._ctx.admin_db.transaction() as session:
            await self._check_transition(session, agent_id, ActorVerb.APPROVE)
            agent = await AgentRepository.set_approval(session, agent_id, approved_by=identity.sub)
            existing_grants = await ActorScopeGrantRepository.list_for_actor(
                session, agent_id, actor_type=ActorType.AGENT
            )
            if not existing_grants:
                for scope in DEFAULT_AGENT_SCOPES:
                    await ActorScopeGrantRepository.grant(
                        session,
                        actor_id=agent_id,
                        actor_type=ActorType.AGENT,
                        scope=scope,
                        granted_by=identity.sub,
                        created_by=identity.sub,
                    )
                await record_audit(
                    session,
                    action=AuditAction.GRANT,
                    target_type=AuditTargetType.AGENT,
                    target_id=agent_id,
                    actor_type=identity.actor_type,
                    actor_id=identity.sub,
                    after={"scopes": list(DEFAULT_AGENT_SCOPES)},
                    reason="default_scopes",
                    origin=identity.origin.value,
                )
            await record_audit(
                session,
                action=AuditAction.APPROVE,
                target_type=AuditTargetType.AGENT,
                target_id=agent_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                after={"owner_id": agent.owner_id},
                origin=identity.origin.value,
            )
            await emit_event_best_effort(
                session,
                type=EventType.AGENT_REGISTRATION_APPROVED,
                severity=EventSeverity.INFO,
                summary=f"Agent {agent_id} registration approved",
                created_by=identity.sub,
                actor_id=identity.sub,
                actor_type=identity.actor_type.value,
            )
        return AgentView.model_validate(agent)

    async def deny(self, agent_id: str, *, reason: str, identity: Identity) -> AgentView:
        async with self._ctx.admin_db.transaction() as session:
            await self._check_transition(session, agent_id, ActorVerb.DENY)
            agent = await AgentRepository.set_denial(
                session, agent_id, reason=reason, denied_by=identity.sub
            )
            await record_audit(
                session,
                action=AuditAction.DENY,
                target_type=AuditTargetType.AGENT,
                target_id=agent_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                reason=reason,
                origin=identity.origin.value,
            )
            await emit_event_best_effort(
                session,
                type=EventType.AGENT_REGISTRATION_DENIED,
                severity=EventSeverity.INFO,
                summary=f"Agent {agent_id} registration denied",
                created_by=identity.sub,
                actor_id=identity.sub,
                actor_type=identity.actor_type.value,
            )
        return AgentView.model_validate(agent)

    async def disable(self, agent_id: str, *, identity: Identity) -> None:
        async with self._ctx.admin_db.transaction() as session:
            await self._check_transition(session, agent_id, ActorVerb.DISABLE)
            await AgentRepository.update_status(session, agent_id, ActorStatus.DISABLED)
            await record_audit(
                session,
                action=AuditAction.DISABLE,
                target_type=AuditTargetType.AGENT,
                target_id=agent_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                origin=identity.origin.value,
            )

    async def enable(self, agent_id: str, *, identity: Identity) -> None:
        async with self._ctx.admin_db.transaction() as session:
            await self._check_transition(session, agent_id, ActorVerb.ENABLE)
            await AgentRepository.update_status(session, agent_id, ActorStatus.ACTIVE)
            await record_audit(
                session,
                action=AuditAction.ENABLE,
                target_type=AuditTargetType.AGENT,
                target_id=agent_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                origin=identity.origin.value,
            )

    async def archive(self, agent_id: str, *, identity: Identity) -> None:
        async with self._ctx.admin_db.transaction() as session:
            agent = await AgentRepository.get_by_id(session, agent_id)
            if agent is None:
                raise ActorNotFoundError(agent_id)
            if agent.status == ActorStatus.ARCHIVED:
                raise InvalidTransitionError(agent_id, ActorStatus.ARCHIVED, "archive")
            await AgentRepository.archive(session, agent_id)
            await ActorScopeGrantRepository.revoke_all(session, agent_id)
            await AgentToolkitBindingRepository.delete_for_agent(session, agent_id)
            await record_audit(
                session,
                action=AuditAction.ARCHIVE,
                target_type=AuditTargetType.AGENT,
                target_id=agent_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                origin=identity.origin.value,
            )

    async def list_toolkits(self, agent_id: str, *, identity: Identity) -> list[ToolkitBindingView]:
        await self.get_agent(agent_id, identity=identity)
        async with self._ctx.admin_db.session() as session:
            bindings = await AgentToolkitBindingRepository.list_for_agent(session, agent_id)
        views = [ToolkitBindingView.model_validate(b) for b in bindings]
        # Enrich each binding from the control DB with (a) a human-readable
        # toolkit name so an agent can map an opaque `tk_…` id to something it can
        # show its operator (issue #686), and (b) the APIs the toolkit's bound
        # credentials serve, so `whoami` tells the agent what it can already call
        # and can skip a redundant provisioning plan / a throwaway denied execute.
        # Names/serves live in the control DB; the bindings above are already
        # scoped to this agent, so we only resolve for toolkits the caller is
        # bound to. Failure to reach the control DB is non-fatal.
        toolkit_ids = [v.toolkit_id for v in views]
        if toolkit_ids and self._ctx.is_db_allowed("control"):
            async with self._ctx.control_db.session() as session:
                names = await ToolkitNameRepository.get_names_for_ids(session, toolkit_ids)
            served = await self._served_apis_by_toolkit(toolkit_ids)
            for view in views:
                view.name = names.get(view.toolkit_id)
                view.serves = served.get(view.toolkit_id, [])
        return views

    async def _served_apis_by_toolkit(self, toolkit_ids: list[str]) -> dict[str, list[ServedApi]]:
        """Resolve, per toolkit, the APIs its bound credentials serve.

        Toolkit→credential bindings and credentials live in the control DB, so
        this queries it directly with raw SQL — the auth module must not import
        control ORM (enforced by tests/arch/test_module_boundaries). Mirrors the
        cross-boundary raw-SQL pattern of control's PrerequisiteRepository. The
        (vendor, name, version) tuple lets an agent reading `whoami` see which
        APIs it can already call and skip a redundant provisioning plan / a
        throwaway denied execute.
        """
        if not toolkit_ids:
            return {}
        query = text(
            "SELECT tcb.toolkit_id, c.api_vendor, c.api_name, c.api_version "
            "FROM toolkit_credential_bindings tcb "
            "JOIN credentials c ON c.id = tcb.credential_id "
            "WHERE tcb.toolkit_id IN :toolkit_ids"
        ).bindparams(bindparam("toolkit_ids", expanding=True))
        result: dict[str, list[ServedApi]] = {}
        async with self._ctx.control_db.session() as session:
            rows = await session.execute(query, {"toolkit_ids": toolkit_ids})
            for toolkit_id, vendor, name, version in rows.all():
                result.setdefault(toolkit_id, []).append(
                    ServedApi(api_vendor=vendor, api_name=name, api_version=version)
                )
        return result

    async def bind_toolkit(
        self, agent_id: str, *, toolkit_id: str, identity: Identity
    ) -> ToolkitBindingView:
        await self.get_agent(agent_id, identity=identity)
        async with self._ctx.admin_db.transaction() as session:
            try:
                binding = await AgentToolkitBindingRepository.bind(
                    session, agent_id=agent_id, toolkit_id=toolkit_id, created_by=identity.sub
                )
            except IntegrityError:
                raise ToolkitBindingConflictError(agent_id, toolkit_id) from None
            await record_audit(
                session,
                action=AuditAction.GRANT,
                target_type=AuditTargetType.AGENT,
                target_id=agent_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                target_parent_id=toolkit_id,
                reason="bind_toolkit",
                origin=identity.origin.value,
            )
            await emit_event_best_effort(
                session,
                type=EventType.TOOLKIT_BOUND_TO_AGENT,
                severity=EventSeverity.INFO,
                summary=f"Toolkit {toolkit_id} bound to agent {agent_id}",
                created_by=identity.sub,
                actor_id=identity.sub,
                actor_type=identity.actor_type.value,
            )
        return ToolkitBindingView.model_validate(binding)

    async def unbind_toolkit(self, agent_id: str, *, toolkit_id: str, identity: Identity) -> None:
        await self.get_agent(agent_id, identity=identity)
        async with self._ctx.admin_db.transaction() as session:
            removed = await AgentToolkitBindingRepository.unbind(
                session, agent_id=agent_id, toolkit_id=toolkit_id
            )
            if not removed:
                raise ToolkitBindingNotFoundError(agent_id, toolkit_id)
            await record_audit(
                session,
                action=AuditAction.REVOKE,
                target_type=AuditTargetType.AGENT,
                target_id=agent_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                target_parent_id=toolkit_id,
                reason="unbind_toolkit",
                origin=identity.origin.value,
            )
            await emit_event_best_effort(
                session,
                type=EventType.TOOLKIT_UNBOUND_FROM_AGENT,
                severity=EventSeverity.INFO,
                summary=f"Toolkit {toolkit_id} unbound from agent {agent_id}",
                created_by=identity.sub,
                actor_id=identity.sub,
                actor_type=identity.actor_type.value,
            )

    async def get_scopes(self, agent_id: str, *, identity: Identity) -> list[str]:
        await self.get_agent(agent_id, identity=identity)
        async with self._ctx.admin_db.session() as session:
            grants = await ActorScopeGrantRepository.list_for_actor(
                session, agent_id, actor_type=ActorType.AGENT
            )
        return [g.scope for g in grants]

    async def replace_scopes(
        self, agent_id: str, scopes: list[str], *, identity: Identity
    ) -> list[str]:
        async with self._ctx.admin_db.transaction() as session:
            agent = await AgentRepository.get_by_id(session, agent_id)
            if agent is None:
                raise ActorNotFoundError(agent_id)
            if agent.status == ActorStatus.ARCHIVED:
                raise InvalidTransitionError(agent_id, ActorStatus.ARCHIVED, "replace_scopes")
            await ActorScopeGrantRepository.revoke_all(session, agent_id)
            scopes = list(dict.fromkeys(scopes))
            for scope in scopes:
                await ActorScopeGrantRepository.grant(
                    session,
                    actor_id=agent_id,
                    actor_type=ActorType.AGENT,
                    scope=scope,
                    granted_by=identity.sub,
                    created_by=identity.sub,
                )
            await record_audit(
                session,
                action=AuditAction.GRANT,
                target_type=AuditTargetType.AGENT,
                target_id=agent_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                after={"scopes": scopes},
                reason="replace_scopes",
                origin=identity.origin.value,
            )
        return scopes

    async def update_agent(
        self,
        agent_id: str,
        *,
        update_data: dict[str, str | None],
        identity: Identity,
    ) -> AgentView:
        try:
            async with self._ctx.admin_db.transaction() as session:
                agent = await AgentRepository.get_by_id_for_update(session, agent_id)
                if agent is None:
                    raise ActorNotFoundError(agent_id)
                if agent.status == ActorStatus.ARCHIVED:
                    raise InvalidTransitionError(agent_id, ActorStatus.ARCHIVED, "update")
                before = {k: getattr(agent, k) for k in update_data}
                agent = await AgentRepository.update_agent(session, agent_id, **update_data)
                after = {k: getattr(agent, k) for k in update_data}
                await record_audit(
                    session,
                    action=AuditAction.UPDATE,
                    target_type=AuditTargetType.AGENT,
                    target_id=agent_id,
                    actor_type=identity.actor_type,
                    actor_id=identity.sub,
                    before=before,
                    after=after,
                    origin=identity.origin.value,
                )
        except DatabaseIntegrityError:
            raise InvalidOwnerError(update_data.get("owner_id") or "") from None
        return AgentView.model_validate(agent)

    async def _check_transition(
        self, session: AsyncSession, agent_id: str, verb: ActorVerb
    ) -> None:
        agent = await AgentRepository.get_by_id(session, agent_id)
        if agent is None:
            raise ActorNotFoundError(agent_id)
        if agent.status == ActorStatus.ARCHIVED:
            raise InvalidTransitionError(agent_id, ActorStatus.ARCHIVED, verb)
        allowed_from = _VALID_TRANSITIONS[verb]
        if agent.status not in allowed_from:
            raise InvalidTransitionError(agent_id, agent.status, verb)
