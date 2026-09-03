"""OAuth consent-grant endpoints — revoke + per-agent listing (phase-3a §4.6/§4.8).

The ``:revoke`` verb shipped in 3a-3; 3a-5 adds the per-agent "Connected
clients" listing. The admin cross-view lives on the admin router
(``GET /admin/oauth-grants``).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Response

from jentic_one.admin.services.schemas.oauth_grants import OAuthGrantView
from jentic_one.auth.services.oauth_grant_service import OAuthGrantService
from jentic_one.auth.web.schemas.oauth_grants import (
    OAuthGrantListResponse,
    OAuthGrantResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web.deps import get_ctx
from jentic_one.shared.web.openapi_responses import not_found

router = APIRouter()


def get_oauth_grant_service(ctx: Context = Depends(get_ctx)) -> OAuthGrantService:
    return OAuthGrantService(ctx)


def grant_response(view: OAuthGrantView) -> OAuthGrantResponse:
    return OAuthGrantResponse(
        id=view.id,
        oauth_client_id=view.oauth_client_id,
        client_name=view.client_name,
        client_origin=view.client_origin,
        user_id=view.user_id,
        agent_id=view.agent_id,
        scopes=view.scopes,
        status=view.status,
        created_at=view.created_at,
        revoked_at=view.revoked_at,
        last_used_at=view.last_used_at,
        can_revoke=view.can_revoke,
    )


@router.get(
    "/agents/{agent_id}/oauth-grants",
    summary="List agent OAuth grants",
    responses=not_found(),
)
async def list_agent_oauth_grants(
    agent_id: str,
    identity: Identity = get_current_identity(),
    grant_svc: OAuthGrantService = Depends(get_oauth_grant_service),
    status: Literal["active", "revoked"] | None = Query(
        default=None, description="Filter by grant lifecycle state."
    ),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
) -> OAuthGrantListResponse:
    """List OAuth consent grants binding clients to this agent (§4.8).

    The "Connected clients" surface: every grant carries the client's display
    name and redirect-URI origin, the granted scopes, the consenting user,
    and created/last-used timestamps. Allowed for the agent's owner or an
    admin — authorization is enforced in the service layer, mirroring the
    ``:revoke`` semantics.
    """
    page = await grant_svc.list_grants_for_agent(
        agent_id, identity=identity, status=status, limit=limit, cursor=cursor
    )
    return OAuthGrantListResponse(
        data=[grant_response(v) for v in page.data],
        has_more=page.has_more,
        next_cursor=page.next_cursor,
    )


@router.post(
    "/oauth-grants/{grant_id}:revoke",
    status_code=204,
    summary="Revoke OAuth grant",
    responses=not_found(),
)
async def revoke_oauth_grant(
    grant_id: str,
    identity: Identity = get_current_identity(),
    grant_svc: OAuthGrantService = Depends(get_oauth_grant_service),
) -> Response:
    """Revoke a consent→agent grant — one of the three §4.6 kill radii.

    Allowed for the grant's owner (the consenting user) or an admin. Marks
    the grant ``revoked`` and revokes every outstanding access/refresh token
    minted under it in the same transaction; the live resolvers also re-check
    grant status on every verdict (belt + braces). The client's next token
    use or refresh fails closed. Idempotent on an already-revoked grant.
    """
    await grant_svc.revoke_grant(grant_id, identity=identity)
    return Response(status_code=204)
