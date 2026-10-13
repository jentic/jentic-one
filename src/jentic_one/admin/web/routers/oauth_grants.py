"""OAuth grants admin router — the admin cross-view over consent→agent grants."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query

from jentic_one.admin.services.oauth_grant_admin_service import OAuthGrantAdminService
from jentic_one.admin.services.schemas.oauth_grants import OAuthGrantView
from jentic_one.admin.web.deps import get_oauth_grant_admin_service
from jentic_one.admin.web.schemas.oauth_grants import (
    OAuthGrantAdminListResponse,
    OAuthGrantAdminResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web import openapi_examples as ex

router = APIRouter()


def _to_response(view: OAuthGrantView) -> OAuthGrantAdminResponse:
    return OAuthGrantAdminResponse(
        id=view.id,
        oauth_client_id=view.oauth_client_id,
        client_name=view.client_name,
        client_origin=view.client_origin,
        user_id=view.user_id,
        agent_id=view.agent_id,
        agent_status=view.agent_status,
        scopes=view.scopes,
        status=view.status,
        created_at=view.created_at,
        revoked_at=view.revoked_at,
        last_used_at=view.last_used_at,
        can_revoke=view.can_revoke,
    )


@router.get("/admin/oauth-grants", summary="List OAuth grants")
async def list_oauth_grants(
    identity: Identity = get_current_identity(required_permissions=["oauth-clients:read"]),
    svc: OAuthGrantAdminService = Depends(get_oauth_grant_admin_service),
    client_id: str | None = Query(
        default=None,
        description="Filter by the client's public client_id.",
        openapi_examples=ex.example(ex.OAUTH_PUBLIC_CLIENT_ID),
    ),
    agent_id: str | None = Query(
        default=None, description="Filter by bound agent.", openapi_examples=ex.example(ex.AGENT_ID)
    ),
    user_id: str | None = Query(
        default=None,
        description="Filter by consenting user.",
        openapi_examples=ex.example(ex.USER_ID),
    ),
    status: Literal["active", "revoked"] | None = Query(
        default=None,
        description="Filter by grant lifecycle state.",
        openapi_examples=ex.example("active"),
    ),
    limit: int = Query(default=50, ge=1, le=200, openapi_examples=ex.example(25)),
    cursor: str | None = Query(default=None, openapi_examples=ex.example(ex.CURSOR)),
) -> OAuthGrantAdminListResponse:
    """List consent→agent grants across all clients and agents.

    The admin cross-view over the grant registry: filter by client, agent,
    consenting user, or status. Each item carries the client's display name
    and redirect-URI origin plus the consenting ``user_id`` — after an agent
    ownership transfer the grant stays with the original consenter, so this
    column is how an admin spots stranded grants.

    Scoped to the caller: org admins see every grant. Anyone else sees grants
    they consented to and grants bound to agents they own.
    """
    page = await svc.list_grants(
        identity=identity,
        client_id=client_id,
        agent_id=agent_id,
        user_id=user_id,
        status=status,
        limit=limit,
        cursor=cursor,
    )
    return OAuthGrantAdminListResponse(
        data=[_to_response(v) for v in page.data],
        has_more=page.has_more,
        next_cursor=page.next_cursor,
    )
