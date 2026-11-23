"""Actors router — unified actor directory for UI caching."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import StringConstraints

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
from jentic_one.shared.web import openapi_examples as ex

router = APIRouter()

#: Most ids one ``GET /actors/lookup`` call resolves; callers batch beyond it.
MAX_LOOKUP_IDS = 100

#: Longest id ``GET /actors/lookup`` accepts; actor ids are far shorter.
MAX_LOOKUP_ID_LENGTH = 64


@router.get("/actors", summary="List users and agents")
async def list_actors(
    identity: Identity = get_current_identity(required_permissions=["users:read"]),
    actor_svc: ActorService = Depends(get_actor_service),
    cursor: str | None = Query(
        default=None,
        description="Opaque cursor from a previous page's `next_cursor`; omit for the first page.",
        openapi_examples=ex.example(ex.CURSOR),
    ),
    limit: int = Query(
        default=1000,
        ge=1,
        le=5000,
        description="Maximum number of actors to return (1-5000, default 1000).",
        openapi_examples=ex.example(25),
    ),
) -> ActorListResponse:
    """List users and agents with the minimal fields needed to label them.

    Each entry gives `id`, `actor_type` (`user` or `agent`), display `name`
    and `active`. Requires `users:read`. Results are scoped to the caller:
    `org:admin` lists everyone; anyone else lists only themselves and the
    agents they may see (their own, or with `owner:agents:read` their owner's
    agents). Cursor-paginated (`cursor`, `limit`). To label specific ids you
    already hold, use `GET /actors/lookup`.
    """
    page = await actor_svc.list_all(cursor=cursor, limit=limit, identity=identity)
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


@router.get("/actors/lookup", summary="Resolve actor names by ID")
async def lookup_actors(
    identity: Identity = get_current_identity(),
    actor_svc: ActorService = Depends(get_actor_service),
    ids: list[Annotated[str, StringConstraints(max_length=MAX_LOOKUP_ID_LENGTH)]] = Query(
        alias="id",
        min_length=1,
        max_length=MAX_LOOKUP_IDS,
        description=(
            f"Actor id to resolve; repeat the parameter for several ids "
            f"(at most {MAX_LOOKUP_IDS} per call, each at most "
            f"{MAX_LOOKUP_ID_LENGTH} characters)."
        ),
        openapi_examples=ex.example([ex.AGENT_ID, ex.USER_ID]),
    ),
) -> ActorLookupResponse:
    """Resolve user and agent ids to display names.

    Pass each id as a repeated `id` parameter. Returns `id`, `actor_type`,
    `name` and `active` for each id the caller may see. Any signed-in caller
    may call it, so a UI can label the owners, approvers and actors it
    already holds by id without `users:read`.

    Agents resolve when visible to the caller: itself, its own agents, its
    owner's agents with `owner:agents:read`, or every agent for `org:admin`.
    Users resolve when the caller can already see them by id: itself; the
    owner, registrant, approver or denier of an agent it can see, and the
    API key creator and key rotation/revocation actor of an agent it owns
    (with `agents:read`); the actor on an event it can see (with
    `events:read`); the decider of a held execution it can see; the
    consenting user of an OAuth grant bound to an agent it owns; the creator
    of a credential it can read (with `credentials:read` or
    `owner:credentials:read`); and, with `oauth-clients:read`, the
    consenting user of a grant bound to the caller itself and the creator of
    any OAuth client. `org:admin` resolves every user.
    Ids that match no visible actor are silently left
    out, exactly like unknown ids. Listing the whole directory needs
    `users:read` on `GET /actors`.
    """
    views = await actor_svc.lookup(ids, identity=identity)
    return ActorLookupResponse(
        data=[
            ActorLookupEntryResponse(id=v.id, actor_type=v.actor_type, name=v.name, active=v.active)
            for v in views
        ]
    )
