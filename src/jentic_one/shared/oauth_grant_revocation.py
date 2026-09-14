"""The single grant-revocation body — grant flip + token sweep + audit + event.

Lives in the **shared** tier (like :mod:`jentic_one.shared.audit` and
:mod:`jentic_one.shared.events`, which also wrap admin repositories) so every
consumer can reach it: the auth tier's callers (the manual ``:revoke`` kill
switch, the G10 ownership-transfer sweep, the #1340 agent-archive sweep, and
the RFC 7009 refresh-token full disconnect) import it via
:mod:`jentic_one.auth.services.oauth_grant_service`, and the admin tier's
client hard-delete (``OAuthClientService.delete``) calls it directly — admin
must not import auth (``tests/arch/test_module_boundaries``), and admin
service modules must not take ``AsyncSession`` parameters
(``tests/arch/test_admin_services_no_sqlalchemy``), while the shared
audit/events helpers already do.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.oauth_client_grants import OAuthClientGrant
from jentic_one.admin.repos.access_token_repo import AccessTokenRepository
from jentic_one.admin.repos.oauth_client_grant_repo import OAuthClientGrantRepository
from jentic_one.admin.repos.refresh_token_repo import RefreshTokenRepository
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit
from jentic_one.shared.events import emit_event_best_effort
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.models.oauth_clients import OAuthGrantStatus


async def revoke_grant_and_sweep_tokens(
    session: AsyncSession,
    grant: OAuthClientGrant,
    *,
    actor_type: ActorType,
    actor_id: str,
    origin: str | None,
    audit_reason: str,
    summary: str,
    event_reason: str | None = None,
) -> bool:
    """Flip one grant row + sweep its tokens + audit + event, in the caller's session.

    THE single revocation body — the manual ``:revoke`` kill switch, the
    ownership-transfer sweep (G10), the RFC 7009 refresh-token full
    disconnect (G11, :mod:`jentic_one.auth.services.oauth_revocation_service`),
    and the OAuth-client hard delete
    (:meth:`jentic_one.admin.services.oauth_client_service.OAuthClientService.delete`)
    all run through here, so the token kill switch and the emitted
    ``oauth_grant.revoked`` event can never drift between the causes.
    Flush-only: it joins whatever transaction the caller owns.
    Returns False (writing no audit/event) when the grant was already revoked;
    the token sweep re-runs regardless (idempotent belt).
    """
    newly_revoked = grant.status == OAuthGrantStatus.ACTIVE.value and (
        await OAuthClientGrantRepository.revoke(session, grant.id)
    )
    swept_access = await AccessTokenRepository.revoke_by_grant(session, grant.id)
    swept_refresh = await RefreshTokenRepository.revoke_by_grant(session, grant.id)
    if not newly_revoked:
        return False

    await record_audit(
        session,
        action=AuditAction.REVOKE,
        target_type=AuditTargetType.OAUTH_GRANT,
        target_id=grant.id,
        actor_type=actor_type,
        actor_id=actor_id,
        after={
            "oauth_client_id": grant.oauth_client_id,
            "agent_id": grant.agent_id,
            "swept_access_tokens": swept_access,
            "swept_refresh_tokens": swept_refresh,
        },
        reason=audit_reason,
        origin=origin,
    )
    data: dict[str, object] = {
        "grant_id": grant.id,
        "oauth_client_id": grant.oauth_client_id,
        "agent_id": grant.agent_id,
        "user_id": grant.user_id,
    }
    if event_reason is not None:
        data["reason"] = event_reason
    await emit_event_best_effort(
        session,
        type=EventType.OAUTH_GRANT_REVOKED,
        severity=EventSeverity.INFO,
        summary=summary,
        requires_action=False,
        data=data,
        created_by=actor_id,
    )
    return True
