"""Consent→agent grant lifecycle service.

Owns the ``oauth_client_grants`` rows: minting at consent-approve (with the
(client, agent) pair-collapse), and the per-grant kill switch (``:revoke`` + token
sweep). Token *resolution* gates live in :mod:`token_service` and the broker
resolver — this service is the write side.
"""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.oauth_client_grants import OAuthClientGrant
from jentic_one.admin.repos import (
    AgentRepository,
    OAuthClientGrantRepository,
)
from jentic_one.admin.services.oauth_grant_admin_service import (
    GRANT_REVOKE_ADMIN_PERMISSIONS,
    OAuthGrantAdminService,
    viewer_can_revoke,
)
from jentic_one.admin.services.schemas.oauth_grants import OAuthGrantView
from jentic_one.auth.services.errors import (
    ActorNotFoundError,
    ConsentAgentNotEligibleError,
    OAuthGrantAccessDeniedError,
    OAuthGrantNotFoundError,
)
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import OAUTH_CLIENTS_READ
from jentic_one.shared.context import Context
from jentic_one.shared.events import emit_event_best_effort
from jentic_one.shared.models import ActorStatus, ActorType
from jentic_one.shared.models.events import EventSeverity, EventType

# Re-exported: the single revocation body moved to the shared tier so the
# admin client hard-delete can run it too (admin must not import auth); every
# auth caller keeps importing it from here.
from jentic_one.shared.oauth_grant_revocation import (
    revoke_grant_and_sweep_tokens as revoke_grant_and_sweep_tokens,
)
from jentic_one.shared.pagination import Page

logger = structlog.get_logger(__name__)

#: Read-side admin set (grant listings): the ``:revoke`` write pair
#: (:data:`GRANT_REVOKE_ADMIN_PERMISSIONS` — the single revoke-predicate
#: definition, shared with ``viewer_can_revoke``) plus the read-only half of
#: the client-lifecycle permission pair — the same permission that gates
#: ``GET /admin/oauth-clients`` and the ``/admin/oauth-grants`` cross-view,
#: so a caller who can see grants there can see them per-agent.
_ADMIN_READ_PERMISSIONS: frozenset[str] = GRANT_REVOKE_ADMIN_PERMISSIONS | {OAUTH_CLIENTS_READ}

#: The revocation cause stamped (audit ``reason`` + event ``data.reason``) on
#: grants swept by an agent ownership transfer (G10, #1222) — distinguishes a
#: transfer-revocation from the manual ``:revoke`` (whose audit reason stays
#: ``"oauth grant revoked"`` and whose event data carries no ``reason`` key,
#: following the ``OVERLAY_DEPRECATED`` cause-in-data pattern).
AGENT_TRANSFER_REVOCATION_REASON = "agent_ownership_transferred"

#: The revocation cause stamped on grants swept by an agent archive (#1233):
#: archive is terminal (the status enum has no exit from it), so leaving the
#: agent's consent grants ``active`` forever would misreport every "active
#: grants" listing/count on a dead agent. Same cause-in-data pattern as the
#: transfer reason above.
AGENT_ARCHIVE_REVOCATION_REASON = "agent_archived"


async def revoke_active_grants_for_agent(
    session: AsyncSession,
    agent_id: str,
    *,
    identity: Identity,
    audit_reason: str = "oauth grant revoked: agent ownership transferred",
    event_reason: str = AGENT_TRANSFER_REVOCATION_REASON,
    summary_cause: str = "changed owner",
    log_event: str = "oauth_grants_revoked_on_agent_transfer",
) -> int:
    """Revoke EVERY active grant bound to ``agent_id`` — the per-agent sweep.

    Born as the transfer sweep (G10, #1222; the cause parameters default to
    that stamp so the transfer call site reads unchanged): a transferred
    agent must not keep grants consented by its previous owner (the new owner
    could not revoke them self-serve — the ``:revoke`` predicate keys on the
    consenting user). ``AgentService.archive`` reuses it with the archive
    stamp (#1233): archive is terminal, so its grants would otherwise stay
    ``active`` forever. Any new caller MUST pass its own ``audit_reason`` /
    ``event_reason`` / ``summary_cause`` / ``log_event`` — never let a
    different cause masquerade as a transfer. Runs the same per-grant
    revocation body as the manual kill switch, attributed to the actor
    performing the mutation.

    Deliberately NO ``viewer_can_revoke`` check: authority comes from the
    ``agents:write`` gate on the mutation itself. Flush-only and NOT
    best-effort — an exception propagates so a failed sweep rolls the whole
    mutation back rather than leaving a dead/transferred agent with live
    grants. Returns the number of grants revoked.
    """
    grants = await OAuthClientGrantRepository.list_active_for_agent(session, agent_id)
    for grant in grants:
        await revoke_grant_and_sweep_tokens(
            session,
            grant,
            actor_type=identity.actor_type,
            actor_id=identity.sub,
            origin=identity.origin.value,
            audit_reason=audit_reason,
            summary=(
                f"OAuth grant {grant.id} for client '{grant.oauth_client_id}' was "
                f"revoked because agent {agent_id} {summary_cause}"
            ),
            event_reason=event_reason,
        )
    if grants:
        logger.info(
            log_event,
            agent_id=agent_id,
            count=len(grants),
            actor_id=identity.sub,
        )
    return len(grants)


class OAuthGrantService:
    """Mint and revoke consent→agent grants."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def create_grant(
        self,
        *,
        user_id: str,
        oauth_client_id: str,
        agent_id: str,
        scopes: list[str],
        client_name: str | None = None,
    ) -> str:
        """Mint an ``oauth_client_grants`` row at consent-approve.

        Locks the agent row and re-checks the consent predicate (exists +
        ``active`` + owned by ``user_id``) inside the mint transaction —
        raising :class:`ConsentAgentNotEligibleError` on failure — so a
        concurrent ownership transfer serializes against the mint instead of
        racing it. Collapses any prior active row for the (client, agent)
        pair (re-consent revokes the old row and inserts the new one —
        history stays, exactly one active row per pair). Writes the consent
        audit row (the pre-3a ``record_consent_decision`` shape, extended
        with ``agent_id``/``grant_id``) and emits ``oauth_grant.created`` —
        grant creation is deliberately loud. Returns the new grant id.
        """

        async def _write(session: AsyncSession) -> str:
            # Consent-vs-transfer TOCTOU closure: the consent screen's
            # ownership check ran in an earlier, unlocked read, so a transfer
            # can commit in between (the consent handle stays valid for
            # 300 s). Take the SAME row lock the transfer transaction takes
            # (FOR UPDATE in AgentService.update_agent) and re-run the
            # list_consentable_agents predicate here, inside the mint
            # transaction: either the mint commits first (the transfer's
            # sweep then revokes it) or the transfer commits first (this
            # re-check sees the new owner/status and refuses). This lock +
            # re-check is what makes "a LIVE grant's consenter is the
            # agent's current owner" hold.
            agent = await AgentRepository.get_by_id_for_update(session, agent_id)
            if (
                agent is None
                or agent.status != ActorStatus.ACTIVE.value
                or agent.owner_id != user_id
            ):
                raise ConsentAgentNotEligibleError(agent_id)
            prior = await OAuthClientGrantRepository.list_active_for_pair(
                session, oauth_client_id=oauth_client_id, agent_id=agent_id
            )
            for old in prior:
                await OAuthClientGrantRepository.revoke(session, old.id)
            grant = await OAuthClientGrantRepository.create(
                session,
                oauth_client_id=oauth_client_id,
                user_id=user_id,
                agent_id=agent_id,
                scopes=scopes,
                created_by=user_id,
            )
            await record_audit(
                session,
                action=AuditAction.APPROVE,
                target_type=AuditTargetType.OAUTH_CLIENT,
                target_id=oauth_client_id,
                actor_type=ActorType.USER,
                actor_id=user_id,
                after={
                    "scopes": " ".join(scopes),
                    "oauth_client_id": oauth_client_id,
                    "agent_id": agent_id,
                    "grant_id": grant.id,
                },
                reason="oauth consent approved",
                origin=None,
            )
            await emit_event_best_effort(
                session,
                type=EventType.OAUTH_GRANT_CREATED,
                severity=EventSeverity.INFO,
                summary=(
                    f"OAuth client '{client_name or oauth_client_id}' was granted "
                    f"access through agent {agent_id}"
                ),
                # Consent WAS the decision — user-visible notification, not an
                # inbox item awaiting action.
                requires_action=False,
                data={
                    "grant_id": grant.id,
                    "oauth_client_id": oauth_client_id,
                    "agent_id": agent_id,
                    "user_id": user_id,
                    "scopes": scopes,
                },
                created_by=user_id,
            )
            return grant.id

        return await self._ctx.admin_db.run_in_transaction(_write)

    async def revoke_grant(self, grant_id: str, *, identity: Identity) -> None:
        """The per-grant kill switch: row ``revoked`` + token sweep.

        Owner (the consenting user) or admin only. Revokes the grant row and
        every live access/refresh token stamped with this ``oauth_grant_id``
        in the same transaction (belt — the resolvers' live grant gate is the
        braces). Idempotent: revoking an already-revoked grant re-runs the
        sweep but writes no second audit/event.
        """

        async def _write(session: AsyncSession) -> bool:
            grant = await OAuthClientGrantRepository.get_by_id(session, grant_id)
            if grant is None:
                raise OAuthGrantNotFoundError(grant_id)
            if not viewer_can_revoke(grant.user_id, identity):
                raise OAuthGrantAccessDeniedError(grant_id)

            return await revoke_grant_and_sweep_tokens(
                session,
                grant,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                origin=identity.origin.value,
                audit_reason="oauth grant revoked",
                summary=(
                    f"OAuth grant {grant_id} for client '{grant.oauth_client_id}' was revoked"
                ),
            )

        revoked = await self._ctx.admin_db.run_in_transaction(_write)
        if revoked:
            logger.info("oauth_grant_revoked", grant_id=grant_id, actor_id=identity.sub)

    async def list_grants_for_agent(
        self,
        agent_id: str,
        *,
        identity: Identity,
        status: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Page[OAuthGrantView]:
        """The per-agent "Connected clients" listing.

        Owner-or-admin, mirroring ``revoke_grant``'s semantics on the read
        side: the agent's owner sees their agent's grants; anyone else needs
        an admin permission (403, not 404 — agent ids are ksuids, not
        secrets). Items carry the display fields (client name,
        redirect-URI origin, scopes, created, last-used, status) plus the
        consenting ``user_id`` and the viewer's per-item ``can_revoke``
        capability. The two predicates still differ (list keys on the agent's
        current owner, revoke on the grant's consenting user), but a LIVE
        grant's consenter is always the current owner — an invariant that
        holds BECAUSE G10 (#1222) makes an ownership transfer revoke all
        active grants in the transfer transaction AND ``create_grant`` locks
        the agent row and re-checks ownership inside the mint transaction
        (closing the consent-vs-transfer race). The divergence only shows on
        revoked history rows.
        """
        async with self._ctx.admin_db.session() as session:
            agent = await AgentRepository.get_by_id(session, agent_id)
        if agent is None:
            raise ActorNotFoundError(agent_id)
        if agent.owner_id != identity.sub and not (
            _ADMIN_READ_PERMISSIONS & set(identity.permissions)
        ):
            raise OAuthGrantAccessDeniedError(
                agent_id,
                message=f"Not permitted to list OAuth grants for agent '{agent_id}'",
            )

        return await OAuthGrantAdminService(self._ctx).list_grants(
            identity=identity, agent_id=agent_id, status=status, limit=limit, cursor=cursor
        )

    async def get_grant(self, grant_id: str) -> OAuthClientGrant | None:
        """Read one grant row (used by tests and the exchange path)."""
        async with self._ctx.admin_db.session() as session:
            return await OAuthClientGrantRepository.get_by_id(session, grant_id)
