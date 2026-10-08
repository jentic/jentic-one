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
from jentic_one.admin.repos.oauth_client_repo import OAuthClientRepository
from jentic_one.admin.repos.refresh_token_repo import RefreshTokenRepository
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit
from jentic_one.shared.events import emit_event_best_effort, summary_label
from jentic_one.shared.models.events import EventSeverity, EventType
from jentic_one.shared.models.oauth_clients import OAuthGrantStatus


async def revoke_grant_and_sweep_tokens(
    session: AsyncSession,
    grant: OAuthClientGrant,
    *,
    actor_type: str,
    actor_id: str,
    origin: str | None,
    audit_reason: str,
    revocation_cause: str | None = None,
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

    ``revocation_cause`` is the cause half of the summary sentence, read as
    "… was revoked because <cause>" (``None`` for the manual kill switch, whose
    cause is the act itself). The summary is composed HERE and nowhere else —
    see :func:`_revocation_summary` for why.

    ``actor_type`` is the opaque persisted string (an ``ActorType`` value
    or a retired label such as ``service_account`` on a residual token row):
    revocation must never fail on a historical actor type.
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
        summary=await _revocation_summary(session, grant, revocation_cause),
        requires_action=False,
        data=data,
        created_by=actor_id,
    )
    return True


async def _revocation_summary(
    session: AsyncSession, grant: OAuthClientGrant, cause: str | None
) -> str:
    """The one ``oauth_grant.revoked`` sentence, for every revocation cause.

    Composed inside the revocation body, not at the call sites: the four causes
    (manual ``:revoke``, agent sweep, RFC 7009 disconnect, client delete) each
    supply only their cause clause, so there is exactly one definition of how
    the client is named and how wide the sentence can get. Four hand-written
    copies of this sentence is how the client came to be named by its raw
    ``oc_…`` id while the rest of the summary vocabulary names entities through
    :func:`summary_label` (#1543).

    The client row is read here rather than passed in, so no caller can supply
    a different name — or forget to supply one. It is one indexed lookup per
    *newly revoked* grant, after the early return, on a path that already does
    a grant flip, two token sweeps and two inserts.
    ``oauth_client_grants.oauth_client_id`` is a plain column (no FK), so a
    grant outliving its client is expected and degrades to the id.
    """
    client = await OAuthClientRepository.get_by_client_id(session, grant.oauth_client_id)
    label = summary_label(None if client is None else client.name, grant.oauth_client_id)
    sentence = f"OAuth grant {grant.id} for client {label} was revoked"
    return sentence if cause is None else f"{sentence} because {cause}"
