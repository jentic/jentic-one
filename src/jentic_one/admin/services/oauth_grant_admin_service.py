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
from jentic_one.shared.context import Context
from jentic_one.shared.models.oauth_clients import OAuthGrantStatus
from jentic_one.shared.pagination import Page, decode_cursor_str, encode_cursor


class OAuthGrantAdminService:
    """Query consent→agent grant rows, enriched with client display fields."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def list_grants(
        self,
        *,
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
        last-used, status.
        """
        if status is not None and status not in (
            OAuthGrantStatus.ACTIVE.value,
            OAuthGrantStatus.REVOKED.value,
        ):
            raise InvalidInputError(f"unsupported status filter: {status}")

        cursor_dt = None
        if cursor is not None:
            cursor_dt, _ = decode_cursor_str(cursor)

        async with self._ctx.admin_db.session() as session:
            grants = await OAuthClientGrantRepository.list_grants(
                session,
                agent_id=agent_id,
                oauth_client_id=client_id,
                user_id=user_id,
                status=status,
                limit=limit + 1,
                cursor=cursor_dt,
            )
            has_more = len(grants) > limit
            if has_more:
                grants = grants[:limit]
            clients = await OAuthClientRepository.list_by_client_ids(
                session, sorted({g.oauth_client_id for g in grants})
            )

        clients_by_id = {c.client_id: c for c in clients}
        views = [grant_to_view(g, clients_by_id.get(g.oauth_client_id)) for g in grants]

        next_cursor = None
        if has_more and grants:
            next_cursor = encode_cursor(grants[-1].created_at, grants[-1].id)

        return Page(data=views, has_more=has_more, next_cursor=next_cursor)
