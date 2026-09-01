"""OAuth consent-grant endpoints — the per-grant kill switch (phase-3a §4.6/§4.8).

Only the ``:revoke`` verb ships in 3a-3; the grants listing surfaces (per-agent
"Connected clients", admin cross-view) are 3a-5 UI scope.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from jentic_one.auth.services.oauth_grant_service import OAuthGrantService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.web import get_current_identity
from jentic_one.shared.web.deps import get_ctx
from jentic_one.shared.web.openapi_responses import not_found

router = APIRouter()


def get_oauth_grant_service(ctx: Context = Depends(get_ctx)) -> OAuthGrantService:
    return OAuthGrantService(ctx)


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
