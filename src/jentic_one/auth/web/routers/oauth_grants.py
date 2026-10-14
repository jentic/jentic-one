"""OAuth consent-grant endpoints — revoke + per-agent listing.

The ``:revoke`` verb, the per-agent "Connected
clients" listing. The admin cross-view lives on the admin router
(``GET /admin/oauth-grants``).
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Response

from jentic_one.admin.services.schemas.oauth_grants import OAuthGrantView
from jentic_one.auth.services.oauth_grant_service import OAuthGrantService
from jentic_one.auth.web.schemas.oauth_grants import (
    OAuthGrantListResponse,
    OAuthGrantResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web import openapi_examples as ex
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
        agent_status=view.agent_status,
        scopes=view.scopes,
        status=view.status,
        created_at=view.created_at,
        revoked_at=view.revoked_at,
        last_used_at=view.last_used_at,
        can_revoke=view.can_revoke,
    )


@router.get(
    "/agents/{agent_id}/oauth-grants",
    summary="List an agent's connected OAuth clients",
    responses=not_found(),
)
async def list_agent_oauth_grants(
    agent_id: Annotated[
        str, Path(description="ID of the agent.", openapi_examples=ex.example(ex.AGENT_ID))
    ],
    identity: Identity = get_current_identity(),
    grant_svc: OAuthGrantService = Depends(get_oauth_grant_service),
    status: Literal["active", "revoked"] | None = Query(
        default=None,
        description="Filter by grant lifecycle state.",
        openapi_examples=ex.example("active"),
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of grants to return (1-200, default 50).",
        openapi_examples=ex.example(25),
    ),
    cursor: str | None = Query(
        default=None,
        description="Cursor from a previous page's `next_cursor`; omit for the first page.",
        openapi_examples=ex.example(ex.CURSOR),
    ),
) -> OAuthGrantListResponse:
    """List the OAuth clients a user has consented to act as this agent.

    Each grant gives the client's display name and redirect-URI origin, the
    granted scopes, the consenting user, its `status`, created and last-used
    timestamps, and `can_revoke` for the caller. Filter by `status`
    (`active` or `revoked`); results are cursor-paginated. Allowed for the
    agent's owner or an admin. An agent the caller cannot see answers 404,
    the same as one that does not exist. Revoke a grant with
    `POST /oauth-grants/{grant_id}:revoke`.
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
    grant_id: Annotated[
        str,
        Path(description="ID of the OAuth grant.", openapi_examples=ex.example(ex.OAUTH_GRANT_ID)),
    ],
    identity: Identity = get_current_identity(),
    grant_svc: OAuthGrantService = Depends(get_oauth_grant_service),
) -> Response:
    """Revoke a consent→agent grant — one of the three revocation kill radii.

    Allowed for the grant's owner (the consenting user) or an admin. Marks
    the grant ``revoked`` and revokes every outstanding access/refresh token
    minted under it in the same transaction; the live resolvers also re-check
    grant status on every verdict (belt + braces). The client's next token
    use or refresh fails closed. Idempotent on an already-revoked grant.
    """
    await grant_svc.revoke_grant(grant_id, identity=identity)
    return Response(status_code=204)
