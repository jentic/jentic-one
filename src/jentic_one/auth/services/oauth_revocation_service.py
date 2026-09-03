"""RFC 7009 token revocation for registered OAuth clients (G11, phase 3).

The service side of the public-client revocation arm of ``POST /oauth/revoke``:
a form-encoded request carrying ``token`` (+ optional ``token_type_hint`` and
``client_id``). Public clients (``token_endpoint_auth_method='none'``, D5)
cannot present a secret, so "client authentication" is the **lineage match**:
the call revokes anything only when the token row exists AND its
``oauth_client_id`` equals the caller-supplied ``client_id``. Everything else
— unknown token, foreign client, platform-lineage token, already-revoked row —
is a silent no-op, so the endpoint is not a token-validity oracle (RFC 7009
§2.2: "invalid tokens do not cause an error").

Semantics (decision Manuel, 2026-09-03 — full disconnect):

- **Access token** → that single token dies; the grant survives (the client
  re-obtains access via its refresh token).
- **Refresh token** → FULL disconnect: all tokens of the same grant die AND
  the ``oauth_client_grants`` row itself is revoked, so reconnecting requires
  fresh consent. This deliberately exceeds RFC 7009 §2.1 (which only SHOULD
  revoke related refresh/access tokens and says nothing about the underlying
  authorization) to keep ONE revocation semantics everywhere: the UI grant
  kill switch (3a-5), the G10 transfer sweep, and this endpoint all run
  :func:`~jentic_one.auth.services.oauth_grant_service.revoke_grant_and_sweep_tokens`
  — same sweep, same audit shape, same ``oauth_grant.revoked`` event (with the
  cause-in-data ``data.reason``).
"""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.repos import (
    AccessTokenRepository,
    OAuthClientGrantRepository,
    RefreshTokenRepository,
)
from jentic_one.auth.services.oauth_grant_service import revoke_grant_and_sweep_tokens

# The one token-hash definition (sha256 hex) — mint (token_service) and this
# lookup must never drift, so reuse it rather than re-implementing.
from jentic_one.auth.services.token_service import _hash_token
from jentic_one.shared.audit import AuditAction, AuditTargetType, record_audit
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType

logger = structlog.get_logger(__name__)

#: The revocation cause stamped (audit ``reason`` suffix + event
#: ``data.reason``) on grants swept by an RFC 7009 refresh-token revocation —
#: distinguishes a client-initiated disconnect from the manual ``:revoke``
#: (no ``reason`` key) and the G10 transfer sweep
#: (``agent_ownership_transferred``), following the cause-in-data pattern.
RFC7009_CLIENT_REVOCATION_REASON = "rfc7009_client_revocation"

#: RFC 7009 §2.1 ``token_type_hint`` values this server understands. A hint is
#: only a lookup-order optimization — an unknown hint value, a wrong hint, or
#: no hint at all must all still find the token (§2.1: the server MUST extend
#: its search when the hint misses), so ``unsupported_token_type`` is never
#: returned here.
SUPPORTED_TOKEN_TYPE_HINTS = frozenset({"access_token", "refresh_token"})


class OAuthRevocationService:
    """RFC 7009 revocation with client_id lineage binding (public clients)."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def revoke_client_token(
        self,
        token: str,
        *,
        client_id: str | None,
        token_type_hint: str | None = None,
    ) -> None:
        """Revoke ``token`` iff its lineage matches ``client_id``; else no-op.

        Never raises for an unknown/foreign/already-revoked token and never
        reveals whether anything was revoked (the route always answers 200) —
        the RFC 7009 no-oracle posture. The hint only orders the two lookups;
        both token types are always tried.
        """

        async def _write(session: AsyncSession) -> None:
            if token_type_hint == "refresh_token":
                if not await self._try_revoke_refresh(session, token, client_id):
                    await self._try_revoke_access(session, token, client_id)
            else:
                # No hint, access_token hint, or an unknown hint value: try
                # access first, then fall through to refresh (§2.1).
                if not await self._try_revoke_access(session, token, client_id):
                    await self._try_revoke_refresh(session, token, client_id)

        await self._ctx.admin_db.run_in_transaction(_write)

    async def _try_revoke_access(
        self, session: AsyncSession, token: str, client_id: str | None
    ) -> bool:
        """Access-token arm: the single token dies, the grant survives.

        Returns True when the token row was *found* (regardless of whether it
        was revoked — a found-but-foreign or found-but-dead token must not
        fall through to the refresh lookup and must not error).
        """
        at = await AccessTokenRepository.get_by_hash(session, _hash_token(token))
        if at is None:
            return False
        if at.oauth_client_id is None or at.oauth_client_id != client_id:
            # Foreign or platform-lineage token: succeed-as-noop, no oracle.
            return True
        if at.revoked_at is not None:
            return True
        await AccessTokenRepository.revoke(session, at.id)
        await record_audit(
            session,
            action=AuditAction.REVOKE,
            target_type=AuditTargetType.TOKEN,
            target_id=at.token_family_id,
            actor_type=ActorType(at.actor_type),
            actor_id=at.actor_id,
            after={"token_type": "access", "oauth_client_id": at.oauth_client_id},
            reason=f"token revoked by OAuth client ({RFC7009_CLIENT_REVOCATION_REASON})",
            origin=None,
        )
        logger.info(
            "rfc7009_access_token_revoked",
            oauth_client_id=at.oauth_client_id,
            oauth_grant_id=at.oauth_grant_id,
        )
        return True

    async def _try_revoke_refresh(
        self, session: AsyncSession, token: str, client_id: str | None
    ) -> bool:
        """Refresh-token arm: the full disconnect (grant + every grant token).

        Returns True when the token row was *found* (see access arm).
        """
        rt = await RefreshTokenRepository.get_by_hash(session, _hash_token(token))
        if rt is None:
            return False
        if rt.oauth_client_id is None or rt.oauth_client_id != client_id:
            return True
        if rt.revoked_at is not None:
            return True

        # The family sweep is the RFC 7009 §2.1 SHOULD (related access tokens
        # + the refresh token itself); it also covers any grant-less
        # confidential-lineage family.
        await RefreshTokenRepository.revoke_family(session, rt.token_family_id)
        await AccessTokenRepository.revoke_family(session, rt.token_family_id)

        grant = (
            await OAuthClientGrantRepository.get_by_id(session, rt.oauth_grant_id)
            if rt.oauth_grant_id is not None
            else None
        )
        if grant is not None:
            # Full disconnect (exceeds the §2.1 SHOULD deliberately): the
            # shared sweep revokes the grant row and every token of the grant,
            # and emits the same audit/event shape as the UI kill switch and
            # the G10 transfer sweep — cause distinguished via data.reason.
            await revoke_grant_and_sweep_tokens(
                session,
                grant,
                actor_type=ActorType(rt.actor_type),
                actor_id=rt.actor_id,
                origin=None,
                audit_reason="oauth grant revoked: client revoked its refresh token (RFC 7009)",
                summary=(
                    f"OAuth grant {grant.id} for client '{grant.oauth_client_id}' was "
                    f"revoked because the client revoked its refresh token"
                ),
                event_reason=RFC7009_CLIENT_REVOCATION_REASON,
            )
        else:
            await record_audit(
                session,
                action=AuditAction.REVOKE,
                target_type=AuditTargetType.TOKEN,
                target_id=rt.token_family_id,
                actor_type=ActorType(rt.actor_type),
                actor_id=rt.actor_id,
                after={"token_type": "refresh", "oauth_client_id": rt.oauth_client_id},
                reason=f"token family revoked by OAuth client ({RFC7009_CLIENT_REVOCATION_REASON})",
                origin=None,
            )
        logger.info(
            "rfc7009_refresh_token_revoked",
            oauth_client_id=rt.oauth_client_id,
            oauth_grant_id=rt.oauth_grant_id,
            full_disconnect=grant is not None,
        )
        return True
