"""Agent lifecycle management service."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.repos import (
    ActorPermissionGrantRepository,
    AgentCredentialBindingRepository,
    AgentCredentialRepository,
    AgentRepository,
)
from jentic_one.admin.scoping.filters import build_access_filters
from jentic_one.auth.repos import BindingRuleRepository, CredentialRefRepository
from jentic_one.auth.services.agent_permission_ceiling import check_agent_permission_grant
from jentic_one.auth.services.errors import (
    ActorNotFoundError,
    AgentAlreadyOwnedError,
    ClaimActorNotAllowedError,
    ClaimTokenInvalidError,
    CredentialBindingConflictError,
    CredentialBindingNotFoundError,
    CredentialNotVisibleError,
    InvalidOwnerError,
    InvalidTransitionError,
    OwnerTransferForbiddenError,
)
from jentic_one.auth.services.oauth_grant_service import (
    AGENT_ARCHIVE_REVOCATION_REASON,
    revoke_active_grants_for_agent,
)
from jentic_one.auth.services.registration_service import validate_jwks
from jentic_one.auth.services.schemas.agents import (
    AgentCreatePayload,
    AgentView,
    CredentialBindingView,
)
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import (
    ALL_PERMISSIONS,
    DEFAULT_AGENT_PERMISSIONS,
    ORG_ADMIN,
    OWNER_CREDENTIALS_READ,
)
from jentic_one.shared.context import Context
from jentic_one.shared.db import DatabaseIntegrityError
from jentic_one.shared.events import (
    emit_event_best_effort,
    summary_label,
)
from jentic_one.shared.models import ActorStatus, ActorType, ActorVerb
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.pagination import Page, decode_cursor_str, encode_cursor
from jentic_one.shared.schemas import ServedApiRef

logger = structlog.get_logger(__name__)

_VALID_TRANSITIONS: dict[ActorVerb, dict[ActorStatus, ActorStatus]] = {
    ActorVerb.APPROVE: {ActorStatus.PENDING: ActorStatus.ACTIVE},
    ActorVerb.DENY: {ActorStatus.PENDING: ActorStatus.REJECTED},
    ActorVerb.DISABLE: {ActorStatus.ACTIVE: ActorStatus.DISABLED},
    ActorVerb.ENABLE: {ActorStatus.DISABLED: ActorStatus.ACTIVE},
}

# Verbs whose decision races ``:claim`` for the row: approving an unowned agent
# makes the approver its owner, so the owner check and the write must see the
# same ``owner_id``. ``_check_transition`` locks the row for these.
_ROW_LOCKED_VERBS: frozenset[ActorVerb] = frozenset({ActorVerb.APPROVE, ActorVerb.DENY})


class AgentService:
    """Manages agent lifecycle: create, list, get, approve, deny, disable, enable, archive."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def create(
        self,
        payload: AgentCreatePayload,
        *,
        owner_id: str,
        identity: Identity,
        status: ActorStatus = ActorStatus.ACTIVE,
    ) -> AgentView:
        """Create an agent for ``owner_id``; default posture is immediately ACTIVE.

        ``status=ActorStatus.PENDING`` is the consent-page hybrid arm (P4
        security review): a consenting user WITHOUT ``agents:write`` may still
        mint their first agent mid-flow, but it lands in the same
        awaiting-approval posture as the anonymous ``POST /register`` door —
        status ``pending``, NO permission grants (``approve()`` grants
        ``DEFAULT_AGENT_PERMISSIONS`` on the PENDING→ACTIVE transition, exactly as
        it does for self-registrations), plus the ``agent.self_registered``
        requires-action event so the registration lands in the admins' approval
        queue. The event is append-only history — a decision is surfaced by its
        own ``agent.registration_*`` event, which the console reads to mark the
        row resolved. Any status other than ACTIVE/PENDING is a programming error.
        """
        if status not in (ActorStatus.ACTIVE, ActorStatus.PENDING):
            raise ValueError(f"agents are created active or pending, not {status}")
        permissions_to_grant: list[str] = []
        if status is ActorStatus.ACTIVE:
            if payload.permissions:
                permissions_to_grant = list(dict.fromkeys(payload.permissions))
                check_agent_permission_grant(permissions_to_grant, identity=identity)
            else:
                permissions_to_grant = list(DEFAULT_AGENT_PERMISSIONS)
        async with self._ctx.admin_db.transaction() as session:
            agent = await AgentRepository.create(
                session,
                name=payload.name,
                owner_id=owner_id,
                registered_by=identity.sub,
                description=payload.description,
                created_by=identity.sub,
                status=status,
            )
            for permission in permissions_to_grant:
                await ActorPermissionGrantRepository.grant(
                    session,
                    actor_id=agent.id,
                    actor_type=ActorType.AGENT,
                    permission=permission,
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
                after={
                    "name": payload.name,
                    "owner_id": owner_id,
                    "status": status.value,
                    "permissions": permissions_to_grant,
                },
                origin=identity.origin.value,
            )
            await emit_event_best_effort(
                session,
                type=EventType.AGENT_CREATED,
                severity=EventSeverity.INFO,
                summary=f"Agent {summary_label(agent.name, agent.id)} created",
                data={"agent_id": agent.id},
                created_by=identity.sub,
                actor_id=identity.sub,
                actor_type=identity.actor_type.value,
            )
            if status is ActorStatus.PENDING:
                # Same actionable event as the /register door — without it the
                # self-registration never surfaces to admins, so nobody decides
                # it and the awaiting page polls forever.
                await emit_event_best_effort(
                    session,
                    type=EventType.AGENT_SELF_REGISTERED,
                    severity=EventSeverity.INFO,
                    summary=f"Agent '{payload.name}' created on the consent page and "
                    "awaits approval",
                    requires_action=True,
                    data={"agent_id": agent.id, "agent_name": payload.name},
                    created_by=identity.sub,
                    actor_id=agent.id,
                    actor_type=ActorType.AGENT.value,
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
        """Activate a pending agent.

        Allowed for the agent's owner or an ``org:admin``; an unowned agent
        (an unclaimed self-registration) only for an ``org:admin``, who becomes
        its owner. Anyone else gets ``ActorNotFoundError``.
        """
        async with self._ctx.admin_db.transaction() as session:
            await self._check_transition(session, agent_id, ActorVerb.APPROVE, identity=identity)
            existing_grants = await ActorPermissionGrantRepository.list_for_actor(
                session, agent_id, actor_type=ActorType.AGENT
            )
            # Permissions a self-registration requested become live on approval, so
            # the approver's ceiling applies to them. Requested strings outside
            # the catalogue grant nothing and are left as-is (not a 422: the
            # registrant, not the approver, chose them).
            check_agent_permission_grant(
                [g.permission for g in existing_grants if g.permission in ALL_PERMISSIONS],
                identity=identity,
            )
            agent = await AgentRepository.set_approval(session, agent_id, approved_by=identity.sub)
            if not existing_grants:
                for permission in DEFAULT_AGENT_PERMISSIONS:
                    await ActorPermissionGrantRepository.grant(
                        session,
                        actor_id=agent_id,
                        actor_type=ActorType.AGENT,
                        permission=permission,
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
                    # Historical rows written before the permission rename still
                    # say ``scopes``; new records use the ``permissions`` key.
                    after={"permissions": list(DEFAULT_AGENT_PERMISSIONS)},
                    reason="default_permissions",
                    origin=identity.origin.value,
                )
            await record_audit(
                session,
                action=AuditAction.APPROVE,
                target_type=AuditTargetType.AGENT,
                target_id=agent_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                after={
                    "owner_id": agent.owner_id,
                    "permissions": [g.permission for g in existing_grants]
                    or list(DEFAULT_AGENT_PERMISSIONS),
                },
                origin=identity.origin.value,
            )
            await emit_event_best_effort(
                session,
                type=EventType.AGENT_REGISTRATION_APPROVED,
                severity=EventSeverity.INFO,
                summary=f"Agent '{agent.name}' registration approved",
                # `agent_id` lets the UI deep-link the rail row to the agent's
                # page (the top-level actor here is the deciding USER).
                data={"agent_id": agent_id, "agent_name": agent.name},
                created_by=identity.sub,
                actor_id=identity.sub,
                actor_type=identity.actor_type.value,
            )
        return AgentView.model_validate(agent)

    async def claim(self, agent_id: str, *, token: str, identity: Identity) -> AgentView:
        """Assign ownership of a self-registered agent to the claiming caller.

        The registering human presents the single-use claim token that was minted
        at ``/register`` (see ``auth/core/claim.py``). Any *authenticated human
        user* may claim — the token is the proof, not a role — so a plain member
        can take ownership of the agent they registered. Once owned, the agent
        shows under the caller via the normal scoping filter, and the owner (or
        an ``org:admin``) approves or denies it. Approval keeps the claimant as
        owner because ``owner_id`` is already set.

        Only ``USER`` actors may claim: ``Agent.owner_id`` is a FK to ``users.id``,
        so a non-user actor (an agent) is rejected up front
        with ``ClaimActorNotAllowedError`` rather than being allowed to write a
        non-user id into the users-FK column.

        Ordering note: existence (404) and ownership (409) are checked *before*
        the token, so an authenticated caller who already knows an agent id can
        learn whether it is unclaimed without holding a token. This is a
        deliberate trade-off — it keeps the single-use replay semantics clean, and
        agent ids are unguessable KSUIDs — not the stronger "never reveal which
        agents are claimable" property (which holds only for the no-token-issued
        case, treated as a plain mismatch below).

        Raises ``ClaimActorNotAllowedError`` (non-user actor),
        ``ActorNotFoundError`` (unknown/archived agent),
        ``AgentAlreadyOwnedError`` (already claimed/owned), or
        ``ClaimTokenInvalidError`` (no token issued, mismatch, or expired).
        """
        if identity.actor_type != ActorType.USER:
            raise ClaimActorNotAllowedError(identity.actor_type.value)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        try:
            async with self._ctx.admin_db.transaction() as session:
                agent = await AgentRepository.get_by_id_for_update(session, agent_id)
                if agent is None or agent.status == ActorStatus.ARCHIVED:
                    raise ActorNotFoundError(agent_id)
                if agent.owner_id is not None:
                    raise AgentAlreadyOwnedError(agent_id)
                # Constant-time compare, and treat "no token was ever issued" the
                # same as a mismatch so we never leak which agents are claimable.
                if not agent.claim_token_hash or not hmac.compare_digest(
                    agent.claim_token_hash, token_hash
                ):
                    raise ClaimTokenInvalidError()
                if agent.claim_expires_at is not None and agent.claim_expires_at < datetime.now(
                    UTC
                ):
                    raise ClaimTokenInvalidError("claim_token_expired")
                agent = await AgentRepository.set_owner_from_claim(
                    session, agent, owner_id=identity.sub
                )
                await record_audit(
                    session,
                    action=AuditAction.CLAIM,
                    target_type=AuditTargetType.AGENT,
                    target_id=agent_id,
                    actor_type=identity.actor_type,
                    actor_id=identity.sub,
                    before={"owner_id": None},
                    after={"owner_id": identity.sub},
                    reason="agent_ownership_claim",
                    origin=identity.origin.value,
                )
        except DatabaseIntegrityError:
            # owner_id is a FK to users.id. The actor-type guard above should make
            # this unreachable, but if the caller's sub is ever a non-user id that
            # slips the guard, surface a clean 403 rather than a raw 500.
            raise ClaimActorNotAllowedError(identity.actor_type.value) from None
        return AgentView.model_validate(agent)

    async def deny(self, agent_id: str, *, reason: str, identity: Identity) -> AgentView:
        """Reject a pending agent; same authorization as :meth:`approve`."""
        async with self._ctx.admin_db.transaction() as session:
            await self._check_transition(session, agent_id, ActorVerb.DENY, identity=identity)
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
                summary=f"Agent '{agent.name}' registration denied",
                data={"agent_id": agent_id, "agent_name": agent.name},
                created_by=identity.sub,
                actor_id=identity.sub,
                actor_type=identity.actor_type.value,
            )
        return AgentView.model_validate(agent)

    async def disable(self, agent_id: str, *, identity: Identity) -> None:
        # #1233 (disable arm, DECIDED): disable does NOT sweep oauth_client_grants.
        # Disable is a reversible kill-switch — the enforcement layer already
        # fail-closes everything while disabled (resolvers gate on status,
        # refresh re-checks per rotation), so the grants stay DORMANT and
        # re-enable restores the standing consent without a new consent round
        # (the GitHub app-suspension model). The honesty half of #1233 is
        # fixed at the listing layer instead: counts exclude and listings
        # annotate grants whose agent is non-active.
        async with self._ctx.admin_db.transaction() as session:
            await self._check_transition(session, agent_id, ActorVerb.DISABLE, identity=identity)
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
            await self._check_transition(session, agent_id, ActorVerb.ENABLE, identity=identity)
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
            agent = await self._load_owned_agent(session, agent_id, identity=identity)
            if agent.status == ActorStatus.ARCHIVED:
                raise InvalidTransitionError(agent_id, ActorStatus.ARCHIVED, "archive")
            await AgentRepository.archive(session, agent_id)
            await ActorPermissionGrantRepository.revoke_all(session, agent_id)
            await AgentCredentialBindingRepository.delete_for_agent(session, agent_id)
            # #1233 (archive arm): archive is terminal — the status enum has
            # no exit — so any consent grant left `active` would misreport
            # every "active grants" listing/count on a dead agent forever.
            # Sweep them in the archive's own transaction, reusing the G10
            # per-agent revocation body (row flip + token sweep + audit +
            # event) with an archive stamp. `disable` deliberately does NOT
            # sweep (#1233, decided): it is reversible, grants stay dormant
            # and re-enable restores the standing consent — see disable().
            await revoke_active_grants_for_agent(
                session,
                agent_id,
                identity=identity,
                audit_reason="oauth grant revoked: agent archived",
                event_reason=AGENT_ARCHIVE_REVOCATION_REASON,
                summary_cause="was archived",
                log_event="oauth_grants_revoked_on_agent_archive",
                agent_name=agent.name,
            )
            await record_audit(
                session,
                action=AuditAction.ARCHIVE,
                target_type=AuditTargetType.AGENT,
                target_id=agent_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                origin=identity.origin.value,
            )

    def _can_bind_credential(self, identity: Identity, created_by: str | None) -> bool:
        """Bind authorization for the direct bind path.

        A binding hands the agent the credential's secret at the broker, so
        this is an ownership check, not a read-visibility check: ``org:admin``
        may bind any credential; everyone else only one they created, or — a
        delegated agent holding ``owner:credentials:read`` — one its owner
        created. This is the owner axis of ``credential_owner_scope`` (the
        access-request ``credential:bind`` effect), so both bind paths agree.

        ``credentials:read`` / ``credentials:write`` deliberately do **not**
        widen this: they gate the credential routes, whose rows are still
        owner-scoped, so honouring them here would let any holder bind another
        user's credential to their own agent and have it injected.
        """
        if ORG_ADMIN in identity.permissions:
            return True
        if created_by is None:
            return False
        if created_by == identity.sub:
            return True
        return (
            OWNER_CREDENTIALS_READ in identity.permissions
            and identity.parent_actor_id is not None
            and created_by == identity.parent_actor_id
        )

    @staticmethod
    def _is_own_binding(agent_id: str, identity: Identity) -> bool:
        """True when a non-admin caller is the bound agent itself.

        Suspension is the owner's cut-off, so the agent may not undo it on its
        own binding — neither by resuming nor by purging and re-binding.
        """
        return ORG_ADMIN not in identity.permissions and agent_id == identity.sub

    async def list_credentials(
        self, agent_id: str, *, identity: Identity
    ) -> list[CredentialBindingView]:
        """List direct credential bindings for an agent, name-enriched."""
        await self.get_agent(agent_id, identity=identity)
        async with self._ctx.admin_db.session() as session:
            bindings = await AgentCredentialBindingRepository.list_for_agent(session, agent_id)
        views = [CredentialBindingView.model_validate(b) for b in bindings]
        # Enrich from the control DB with the credential's human-readable name
        # and the API it serves (issue #686). The bindings above are already
        # scoped to this agent. Failure to reach the control DB is non-fatal.
        credential_ids = [v.credential_id for v in views]
        if credential_ids and self._ctx.is_db_allowed("control"):
            async with self._ctx.control_db.session() as session:
                refs = await CredentialRefRepository.get_refs_for_ids(session, credential_ids)
            for view in views:
                ref = refs.get(view.credential_id)
                if ref is None:
                    continue
                view.name = ref.name
                view.serves = [
                    ServedApiRef(
                        api_vendor=ref.api_vendor,
                        api_name=ref.api_name,
                        api_version=ref.api_version,
                    )
                ]
        return views

    async def bind_credential(
        self, agent_id: str, *, credential_id: str, identity: Identity
    ) -> CredentialBindingView:
        """Create a direct agent↔credential binding.

        Verifies the caller can see the target credential before writing the
        binding (control-DB lookup).
        """
        agent = await self.get_agent(agent_id, identity=identity)
        ref = None
        if self._ctx.is_db_allowed("control"):
            async with self._ctx.control_db.session() as session:
                ref = await CredentialRefRepository.get_by_id(session, credential_id)
        if ref is None or not self._can_bind_credential(identity, ref.created_by):
            raise CredentialNotVisibleError(credential_id)
        async with self._ctx.admin_db.transaction() as session:
            try:
                binding = await AgentCredentialBindingRepository.bind(
                    session, agent_id=agent_id, credential_id=credential_id, created_by=identity.sub
                )
            except IntegrityError:
                raise CredentialBindingConflictError(agent_id, credential_id) from None
            await record_audit(
                session,
                action=AuditAction.GRANT,
                target_type=AuditTargetType.CREDENTIAL_BINDING,
                target_id=binding.id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                target_parent_id=agent_id,
                reason="bind_credential",
                origin=identity.origin.value,
            )
            await emit_event_best_effort(
                session,
                type=EventType.CREDENTIAL_BOUND_TO_AGENT,
                severity=EventSeverity.INFO,
                summary=(
                    f"Credential {summary_label(ref.name, credential_id)} bound to agent "
                    f"{summary_label(agent.name, agent_id)}"
                ),
                data={"agent_id": agent_id, "credential_id": credential_id},
                # The binding's subject is the agent, so its owner sees the
                # event; ``actor_id`` records who changed the binding.
                created_by=agent_id,
                actor_id=identity.sub,
                actor_type=identity.actor_type.value,
            )
        view = CredentialBindingView.model_validate(binding)
        view.name = ref.name
        view.serves = [
            ServedApiRef(
                api_vendor=ref.api_vendor, api_name=ref.api_name, api_version=ref.api_version
            )
        ]
        return view

    async def unbind_credential(
        self, agent_id: str, *, credential_id: str, purge: bool, identity: Identity
    ) -> None:
        """Unbind a credential from an agent.

        Default is a reversible suspend (the binding row and its authored
        permission rules survive; the broker derivation will exclude it).
        ``purge=True`` deletes the row outright — the explicit destructive
        path — together with the pair's inline permission rules, so a later
        re-bind starts from default deny instead of resurrecting rules that
        were dormant under an attached rule set. The rules go first: if the
        admin-side delete then fails, the surviving binding has no inline
        rules and denies (fail-closed) rather than the other way round.
        """
        agent = await self.get_agent(agent_id, identity=identity)
        if purge and self._is_own_binding(agent_id, identity):
            # A purge followed by a re-bind would drop a suspension the owner
            # set, so an agent may suspend its own binding but not purge it.
            raise CredentialBindingNotFoundError(agent_id, credential_id)
        if purge and self._ctx.is_db_allowed("control"):
            async with self._ctx.admin_db.session() as session:
                existing = await AgentCredentialBindingRepository.get(
                    session, agent_id=agent_id, credential_id=credential_id
                )
            if existing is None:
                raise CredentialBindingNotFoundError(agent_id, credential_id)
            async with self._ctx.control_db.transaction() as session:
                await BindingRuleRepository.delete_for_binding(
                    session, agent_id=agent_id, credential_id=credential_id
                )
        credential_name = await self._credential_name(credential_id)
        async with self._ctx.admin_db.transaction() as session:
            if purge:
                removed = await AgentCredentialBindingRepository.purge(
                    session, agent_id=agent_id, credential_id=credential_id
                )
            else:
                removed = await AgentCredentialBindingRepository.set_suspended(
                    session, agent_id=agent_id, credential_id=credential_id, suspended=True
                )
            if not removed:
                raise CredentialBindingNotFoundError(agent_id, credential_id)
            await record_audit(
                session,
                action=AuditAction.REVOKE if purge else AuditAction.DISABLE,
                target_type=AuditTargetType.CREDENTIAL_BINDING,
                target_id=credential_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                target_parent_id=agent_id,
                reason="purge_credential_binding" if purge else "suspend_credential_binding",
                origin=identity.origin.value,
            )
            verb = "unbound (purged) from" if purge else "suspended for"
            credential = summary_label(credential_name, credential_id)
            await emit_event_best_effort(
                session,
                type=EventType.CREDENTIAL_UNBOUND_FROM_AGENT,
                severity=EventSeverity.INFO,
                summary=(
                    f"Credential {credential} {verb} agent {summary_label(agent.name, agent_id)}"
                ),
                data={"agent_id": agent_id, "credential_id": credential_id},
                # The binding's subject is the agent, so its owner sees the
                # event; ``actor_id`` records who changed the binding.
                created_by=agent_id,
                actor_id=identity.sub,
                actor_type=identity.actor_type.value,
            )

    async def _credential_name(self, credential_id: str) -> str | None:
        """Best-effort display name for a credential, for an event summary.

        ``None`` when this process has no control-DB access, the credential is
        gone, or the lookup fails; the summary then falls back to the id.
        """
        if not self._ctx.is_db_allowed("control"):
            return None
        try:
            async with self._ctx.control_db.session() as session:
                ref = await CredentialRefRepository.get_by_id(session, credential_id)
        except Exception:
            logger.warning(
                "credential_name_lookup_failed", credential_id=credential_id, exc_info=True
            )
            return None
        return ref.name if ref is not None else None

    async def resume_credential(
        self, agent_id: str, *, credential_id: str, identity: Identity
    ) -> CredentialBindingView:
        """Lift a suspended binding — the reverse of the default unbind.

        Resuming re-grants the agent the credential's secret at the broker,
        so it takes the same credential-ownership check as
        :meth:`bind_credential` on top of agent visibility: a suspension set
        by the credential's owner cannot be lifted by an agent owner who
        could not bind that credential themselves. The agent itself never
        lifts a suspension on its own binding (``org:admin`` aside).
        """
        agent = await self.get_agent(agent_id, identity=identity)
        if self._is_own_binding(agent_id, identity):
            raise CredentialNotVisibleError(credential_id)
        ref = None
        if self._ctx.is_db_allowed("control"):
            async with self._ctx.control_db.session() as session:
                ref = await CredentialRefRepository.get_by_id(session, credential_id)
        if ref is None or not self._can_bind_credential(identity, ref.created_by):
            raise CredentialNotVisibleError(credential_id)
        async with self._ctx.admin_db.transaction() as session:
            updated = await AgentCredentialBindingRepository.set_suspended(
                session, agent_id=agent_id, credential_id=credential_id, suspended=False
            )
            if not updated:
                raise CredentialBindingNotFoundError(agent_id, credential_id)
            binding = await AgentCredentialBindingRepository.get(
                session, agent_id=agent_id, credential_id=credential_id
            )
            await record_audit(
                session,
                action=AuditAction.ENABLE,
                target_type=AuditTargetType.CREDENTIAL_BINDING,
                target_id=credential_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                target_parent_id=agent_id,
                reason="resume_credential_binding",
                origin=identity.origin.value,
            )
            await emit_event_best_effort(
                session,
                type=EventType.CREDENTIAL_BOUND_TO_AGENT,
                severity=EventSeverity.INFO,
                summary=(
                    f"Credential {summary_label(ref.name, credential_id)} binding resumed for "
                    f"agent {summary_label(agent.name, agent_id)}"
                ),
                data={"agent_id": agent_id, "credential_id": credential_id},
                # The binding's subject is the agent, so its owner sees the
                # event; ``actor_id`` records who changed the binding.
                created_by=agent_id,
                actor_id=identity.sub,
                actor_type=identity.actor_type.value,
            )
        return CredentialBindingView.model_validate(binding)

    async def get_permissions(self, agent_id: str, *, identity: Identity) -> list[str]:
        await self.get_agent(agent_id, identity=identity)
        async with self._ctx.admin_db.session() as session:
            grants = await ActorPermissionGrantRepository.list_for_actor(
                session, agent_id, actor_type=ActorType.AGENT
            )
        return [g.permission for g in grants]

    async def replace_permissions(
        self, agent_id: str, permissions: list[str], *, identity: Identity
    ) -> list[str]:
        """Replace the agent's permission grants (owner or ``org:admin`` only).

        Newly added permissions are subject to the agent permission ceiling
        (``check_agent_permission_grant``); permissions the agent already holds may be
        kept, so an owner can narrow a set an admin widened.
        """
        permissions = list(dict.fromkeys(permissions))
        async with self._ctx.admin_db.transaction() as session:
            agent = await self._load_owned_agent(session, agent_id, identity=identity)
            if agent.status == ActorStatus.ARCHIVED:
                raise InvalidTransitionError(agent_id, ActorStatus.ARCHIVED, "replace_permissions")
            existing = [
                g.permission
                for g in await ActorPermissionGrantRepository.list_for_actor(
                    session, agent_id, actor_type=ActorType.AGENT
                )
            ]
            check_agent_permission_grant(permissions, identity=identity, already_held=existing)
            await ActorPermissionGrantRepository.revoke_all(session, agent_id)
            for permission in permissions:
                await ActorPermissionGrantRepository.grant(
                    session,
                    actor_id=agent_id,
                    actor_type=ActorType.AGENT,
                    permission=permission,
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
                # Historical rows written before the permission rename still say
                # ``scopes``; new records use the ``permissions`` key.
                before={"permissions": existing},
                after={"permissions": permissions},
                reason="replace_permissions",
                origin=identity.origin.value,
            )
        return permissions

    async def update_agent(
        self,
        agent_id: str,
        *,
        update_data: dict[str, str | None],
        identity: Identity,
    ) -> AgentView:
        """Partially update an agent; an ``owner_id`` change is a transfer.

        Ownership transfer revokes every active OAuth client grant bound to
        the agent in the SAME transaction (G10, #1222): a grant keys its
        ``:revoke`` predicate on the consenting user, so leaving the old
        owner's consent live would strand a grant the new owner cannot revoke
        self-serve. Fail-safe posture — if the sweep fails, the transfer rolls
        back; the new owner re-consents through the normal flow if the
        connection is still wanted. The agent's key channel (API key, scopes,
        credential bindings) is deliberately untouched.

        Only the owner or an ``org:admin`` caller may update the agent (others
        get a uniform 404); changing ``owner_id`` is ``org:admin``-only.
        """
        try:
            async with self._ctx.admin_db.transaction() as session:
                agent = await self._load_owned_agent(
                    session, agent_id, identity=identity, for_update=True
                )
                if agent.status == ActorStatus.ARCHIVED:
                    raise InvalidTransitionError(agent_id, ActorStatus.ARCHIVED, "update")
                before = {k: getattr(agent, k) for k in update_data}
                owner_transferred = (
                    "owner_id" in update_data and update_data["owner_id"] != agent.owner_id
                )
                if owner_transferred and ORG_ADMIN not in identity.permissions:
                    raise OwnerTransferForbiddenError(agent_id)
                agent = await AgentRepository.update_agent(session, agent_id, **update_data)
                after = {k: getattr(agent, k) for k in update_data}
                if owner_transferred:
                    await revoke_active_grants_for_agent(
                        session, agent_id, identity=identity, agent_name=agent.name
                    )
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

    async def update_jwks(
        self,
        agent_id: str,
        *,
        jwks: dict[str, object],
        identity: Identity,
    ) -> AgentView:
        """Update an agent's JWKS (public keys for JWT-bearer authentication).

        The agent must be active (not pending, disabled, or archived). The JWKS
        is validated to ensure it contains at least one Ed25519 public key and
        no private key material.
        """
        validate_jwks(jwks)
        async with self._ctx.admin_db.transaction() as session:
            agent = await self._load_owned_agent(
                session, agent_id, identity=identity, for_update=True
            )
            if agent.status != ActorStatus.ACTIVE:
                raise InvalidTransitionError(agent_id, agent.status, "update_jwks")
            before_jwks = agent.jwks
            agent.jwks = jwks
            await session.flush()
            await record_audit(
                session,
                action=AuditAction.UPDATE,
                target_type=AuditTargetType.AGENT,
                target_id=agent_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                before={"jwks": "[redacted]" if before_jwks else None},
                after={"jwks": "[redacted]"},
                origin=identity.origin.value,
            )
        return AgentView.model_validate(agent)

    async def _load_owned_agent(
        self,
        session: AsyncSession,
        agent_id: str,
        *,
        identity: Identity,
        for_update: bool = False,
    ) -> Agent:
        """Load an agent the caller may mutate: its owner or an ``org:admin``.

        A missing agent and one owned by someone else raise the same
        ``ActorNotFoundError`` so the response does not reveal which agent
        ids exist outside the caller's ownership.
        """
        if for_update:
            agent = await AgentRepository.get_by_id_for_update(session, agent_id)
        else:
            agent = await AgentRepository.get_by_id(session, agent_id)
        if agent is None:
            raise ActorNotFoundError(agent_id)
        if ORG_ADMIN not in identity.permissions and agent.owner_id != identity.sub:
            raise ActorNotFoundError(agent_id)
        return agent

    async def _check_transition(
        self, session: AsyncSession, agent_id: str, verb: ActorVerb, *, identity: Identity
    ) -> None:
        """Authorize ``verb`` on the agent, then validate the state transition.

        Every verb, approve/deny included, is owner-or-``org:admin``: an agent
        with an owner is decided by that owner or an admin, and an unowned
        (unclaimed self-registered) agent only by an admin. The ownership check
        runs before the state check so a caller who may not act on the agent
        gets the same 404 whatever its status, never a 409 that confirms it
        exists.
        """
        agent = await self._load_owned_agent(
            session, agent_id, identity=identity, for_update=verb in _ROW_LOCKED_VERBS
        )
        if agent.status == ActorStatus.ARCHIVED:
            raise InvalidTransitionError(agent_id, ActorStatus.ARCHIVED, verb)
        allowed_from = _VALID_TRANSITIONS[verb]
        if agent.status not in allowed_from:
            raise InvalidTransitionError(agent_id, agent.status, verb)
