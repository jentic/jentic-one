"""Admin cross-view over OAuth consent→agent grants (phase-3a §4.8).

The write side of the grant lifecycle (minting at consent, the ``:revoke``
kill switch) lives in :mod:`jentic_one.auth.services.oauth_grant_service`;
this service is the read side shared by the §4.8 listing surfaces — the
``GET /admin/oauth-grants`` cross-view and (behind the auth tier's
owner-or-admin check) the per-agent "Connected clients" panel.
"""

from __future__ import annotations

from jentic_one.admin.repos.oauth_client_grant_repo import OAuthClientGrantRepository
from jentic_one.admin.repos.oauth_client_repo import OAuthClientRepository
from jentic_one.admin.services.errors import InvalidInputError
from jentic_one.admin.services.schemas.oauth_grants import OAuthGrantView, grant_to_view
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import OAUTH_CLIENTS_WRITE, ORG_ADMIN
from jentic_one.shared.context import Context
from jentic_one.shared.models.oauth_clients import OAuthGrantStatus
from jentic_one.shared.pagination import Page, decode_cursor_str, encode_cursor

#: Permissions that make a non-owner an admin for grant WRITE operations
#: (``:revoke``). Broader than the usual ``ORG_ADMIN``-only idiom on purpose:
#: ``oauth-clients:write`` holders administer the client lifecycle (the same
#: permission gating the admin oauth-clients router), and a grant is part of
#: that lifecycle. Lives here (not in the auth tier) so both the revoke
#: predicate and the per-item ``can_revoke`` capability read one definition.
GRANT_REVOKE_ADMIN_PERMISSIONS: frozenset[str] = frozenset({ORG_ADMIN, OAUTH_CLIENTS_WRITE})


def viewer_can_revoke(grant_user_id: str, identity: Identity) -> bool:
    """The ``:revoke`` predicate: the grant's consenting user, or a write-set admin.

    THE single definition — ``OAuthGrantService.revoke_grant`` enforces it and
    the listing surfaces surface it per item as ``can_revoke``, so the two can
    never drift. Note the deliberate divergence from the LIST predicate (gap
    G10): listing keys on the agent's CURRENT owner, revoke on the grant's
    consenting user — after an agent ownership transfer the new owner can see
    the grant but not revoke it. That policy decision is still pending; until
    it lands, ``can_revoke`` makes the divergence explicit instead of letting
    the UI advertise a revoke that would 403.
    """
    return grant_user_id == identity.sub or bool(
        GRANT_REVOKE_ADMIN_PERMISSIONS & set(identity.permissions)
    )


class OAuthGrantAdminService:
    """Query consent→agent grant rows, enriched with client display fields."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def list_grants(
        self,
        *,
        identity: Identity,
        agent_id: str | None = None,
        client_id: str | None = None,
        user_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Page[OAuthGrantView]:
        """List grants newest-first, filtered by agent/client/user/status.

        ``client_id`` is the public client identifier (the join key grants
        store, D3). Each item carries the §4.8 display fields: client name,
        redirect-URI origin, scopes, consenting ``user_id`` (G10), created,
        last-used, status — plus ``can_revoke``, the viewer's ``:revoke``
        capability computed per item via :func:`viewer_can_revoke`.
        """
        if status is not None and status not in (
            OAuthGrantStatus.ACTIVE.value,
            OAuthGrantStatus.REVOKED.value,
        ):
            raise InvalidInputError(f"unsupported status filter: {status}")

        cursor_dt = None
        cursor_id = None
        if cursor is not None:
            cursor_dt, cursor_id = decode_cursor_str(cursor)

        async with self._ctx.admin_db.session() as session:
            grants = await OAuthClientGrantRepository.list_grants(
                session,
                agent_id=agent_id,
                oauth_client_id=client_id,
                user_id=user_id,
                status=status,
                limit=limit + 1,
                cursor_created_at=cursor_dt,
                cursor_id=cursor_id,
            )
            has_more = len(grants) > limit
            if has_more:
                grants = grants[:limit]
            clients = await OAuthClientRepository.list_by_client_ids(
                session, sorted({g.oauth_client_id for g in grants})
            )

        clients_by_id = {c.client_id: c for c in clients}
        views = [
            grant_to_view(
                g,
                clients_by_id.get(g.oauth_client_id),
                can_revoke=viewer_can_revoke(g.user_id, identity),
            )
            for g in grants
        ]

        next_cursor = None
        if has_more and grants:
            next_cursor = encode_cursor(grants[-1].created_at, grants[-1].id)

        return Page(data=views, has_more=has_more, next_cursor=next_cursor)
