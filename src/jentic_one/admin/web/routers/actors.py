"""Actors router — unified actor directory for UI caching."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from jentic_one.admin.services.actor_service import ActorService
from jentic_one.admin.web.deps import get_actor_service
from jentic_one.admin.web.schemas.actors import (
    ActorListResponse,
    ActorLookupEntryResponse,
    ActorLookupResponse,
    ActorSummaryResponse,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.web import get_current_identity

router = APIRouter()

#: Most ids one ``GET /actors/lookup`` call resolves; callers batch beyond it.
MAX_LOOKUP_IDS = 100


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


@router.get("/actors/lookup", summary="Resolve actor names by id")
async def lookup_actors(
    identity: Identity = get_current_identity(),
    actor_svc: ActorService = Depends(get_actor_service),
    ids: list[str] = Query(
        alias="id",
        min_length=1,
        max_length=MAX_LOOKUP_IDS,
        description=(
            f"Actor id to resolve; repeat the parameter for several ids "
            f"(at most {MAX_LOOKUP_IDS} per call)."
        ),
    ),
) -> ActorLookupResponse:
    """Resolve user and agent ids to display names for any signed-in caller.

    Returns only ``id``, ``actor_type``, ``name`` and ``active`` for the ids
    asked for, so a caller without ``users:read`` can label the owners,
    approvers and actors it already sees by id. Ids that match no user or agent
    are left out of the response rather than reported as errors. Listing the
    whole directory stays behind ``users:read`` on ``GET /actors``.
    """
    views = await actor_svc.lookup(ids)
    return ActorLookupResponse(
        data=[
            ActorLookupEntryResponse(id=v.id, actor_type=v.actor_type, name=v.name, active=v.active)
            for v in views
        ]
    )
