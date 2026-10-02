"""Actors router — unified actor directory for UI caching."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from jentic_one.admin.services.actor_service import ActorService
from jentic_one.admin.web.deps import get_actor_service
from jentic_one.admin.web.schemas.actors import ActorListResponse, ActorSummaryResponse
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.web import get_current_identity

router = APIRouter()


@router.get("/actors")
async def list_actors(
    identity: Identity = get_current_identity(required_permissions=["users:read"]),
    actor_svc: ActorService = Depends(get_actor_service),
    cursor: str | None = None,
    limit: int = Query(default=1000, ge=1, le=5000),
) -> ActorListResponse:
    """List all actors (users and agents) for UI cache hydration."""
    page = await actor_svc.list_all(cursor=cursor, limit=limit)
    return ActorListResponse(
        data=[
            ActorSummaryResponse(
                id=a.id,
                actor_type=a.actor_type,
                name=a.name,
                active=a.active,
                created_at=a.created_at,
            )
            for a in page.data
        ],
        has_more=page.has_more,
        next_cursor=page.next_cursor,
    )
