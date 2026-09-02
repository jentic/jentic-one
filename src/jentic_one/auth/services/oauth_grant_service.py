"""Consent→agent grant lifecycle service (phase-3a design §4.4, §4.6, §4.8).

Owns the ``oauth_client_grants`` rows: minting at consent-approve (with the
§4.1 pair-collapse), and the per-grant kill switch (``:revoke`` + token
sweep). Token *resolution* gates live in :mod:`token_service` and the broker
resolver — this service is the write side.
"""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.oauth_client_grants import OAuthClientGrant
from jentic_one.admin.repos import (
    AccessTokenRepository,
    AgentRepository,
    OAuthClientGrantRepository,
    RefreshTokenRepository,
)
from jentic_one.admin.services.oauth_grant_admin_service import (
    GRANT_REVOKE_ADMIN_PERMISSIONS,
    OAuthGrantAdminService,
    viewer_can_revoke,
)
from jentic_one.admin.services.schemas.oauth_grants import OAuthGrantView
from jentic_one.auth.services.errors import (
    ActorNotFoundError,
    OAuthGrantAccessDeniedError,
    OAuthGrantNotFoundError,
)
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import OAUTH_CLIENTS_READ
from jentic_one.shared.context import Context
from jentic_one.shared.events import emit_event_best_effort
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.models.oauth_clients import OAuthGrantStatus
from jentic_one.shared.pagination import Page

logger = structlog.get_logger(__name__)

#: Read-side admin set (§4.8 listings): the ``:revoke`` write pair
#: (:data:`GRANT_REVOKE_ADMIN_PERMISSIONS` — the single revoke-predicate
#: definition, shared with ``viewer_can_revoke``) plus the read-only half of
#: the client-lifecycle permission pair — the same permission that gates
#: ``GET /admin/oauth-clients`` and the ``/admin/oauth-grants`` cross-view,
#: so a caller who can see grants there can see them per-agent.
_ADMIN_READ_PERMISSIONS: frozenset[str] = GRANT_REVOKE_ADMIN_PERMISSIONS | {OAUTH_CLIENTS_READ}


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
        """Mint an ``oauth_client_grants`` row at consent-approve (§4.4 step 3).

        Collapses any prior active row for the (client, agent) pair (§4.1:
        re-consent revokes the old row and inserts the new one — history
        stays, exactly one active row per pair). Writes the consent audit row
        (the pre-3a ``record_consent_decision`` shape, extended with
        ``agent_id``/``grant_id``) and emits ``oauth_grant.created`` — grant
        creation is deliberately loud (§4.8). Returns the new grant id.
        """

        async def _write(session: AsyncSession) -> str:
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
                # inbox item awaiting action (§4.8).
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
        """The per-grant kill switch (§4.6): row ``revoked`` + token sweep.

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

            newly_revoked = grant.status == OAuthGrantStatus.ACTIVE.value and (
                await OAuthClientGrantRepository.revoke(session, grant_id)
            )
            swept_access = await AccessTokenRepository.revoke_by_grant(session, grant_id)
            swept_refresh = await RefreshTokenRepository.revoke_by_grant(session, grant_id)
            if not newly_revoked:
                return False

            await record_audit(
                session,
                action=AuditAction.REVOKE,
                target_type=AuditTargetType.OAUTH_GRANT,
                target_id=grant_id,
                actor_type=identity.actor_type,
                actor_id=identity.sub,
                after={
                    "oauth_client_id": grant.oauth_client_id,
                    "agent_id": grant.agent_id,
                    "swept_access_tokens": swept_access,
                    "swept_refresh_tokens": swept_refresh,
                },
                reason="oauth grant revoked",
                origin=identity.origin.value,
            )
            await emit_event_best_effort(
                session,
                type=EventType.OAUTH_GRANT_REVOKED,
                severity=EventSeverity.INFO,
                summary=(
                    f"OAuth grant {grant_id} for client '{grant.oauth_client_id}' was revoked"
                ),
                requires_action=False,
                data={
                    "grant_id": grant_id,
                    "oauth_client_id": grant.oauth_client_id,
                    "agent_id": grant.agent_id,
                    "user_id": grant.user_id,
                },
                created_by=identity.sub,
            )
            return True

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
        """The per-agent "Connected clients" listing (§4.8).

        Owner-or-admin, mirroring ``revoke_grant``'s semantics on the read
        side: the agent's owner sees their agent's grants; anyone else needs
        an admin permission (403, not 404 — agent ids are ksuids, not
        secrets). Items carry the §4.8 display fields (client name,
        redirect-URI origin, scopes, created, last-used, status) plus the
        consenting ``user_id`` and the viewer's per-item ``can_revoke``
        capability, so the G10 ownership-transfer gap is visible AND
        actionable state is honest (list keys on the agent's current owner,
        revoke on the grant's consenting user — they diverge after a
        transfer).
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
