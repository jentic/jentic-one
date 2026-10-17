"""Unified actor directory service."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import ActorDirectoryRepository
from jentic_one.admin.scoping.filters import build_access_filters, build_lookup_user_filters
from jentic_one.admin.services._support.pagination import Page, decode_cursor, encode_cursor
from jentic_one.admin.services.schemas.actors import ActorView
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import ORG_ADMIN
from jentic_one.shared.context import Context
from jentic_one.shared.lookups import CredentialCreatorLookupProtocol
from jentic_one.shared.models import ActorType


class ActorService:
    """Provides a unified read-only view across all actor types.

    ``credential_creators`` resolves which users created a credential the
    caller can read; it lives with the credentials, so the composition root
    passes it in. Without it (an admin process that does not serve the
    credentials), :meth:`lookup` labels no user through that relation.
    """

    def __init__(
        self,
        ctx: Context,
        *,
        credential_creators: CredentialCreatorLookupProtocol | None = None,
    ) -> None:
        self._ctx = ctx
        self._credential_creators = credential_creators

    async def list_all(
        self,
        cursor: str | None = None,
        limit: int = 1000,
        *,
        identity: Identity,
    ) -> Page[ActorView]:
        """List the actor directory, scoped to the caller.

        Both arms are scoped: users by ``build_access_filters(identity, User)``
        (a non-admin lists only themselves) and agents by
        ``build_access_filters(identity, Agent)``. ``org:admin`` lists everyone.
        """
        cursor_dt = None
        cursor_id: str | None = None
        if cursor is not None:
            cursor_dt, cursor_id = decode_cursor(cursor)

        async with self._ctx.admin_db.session() as session:
            rows = await ActorDirectoryRepository.list_all(
                session,
                limit=limit + 1,
                cursor_ts=cursor_dt,
                cursor_id=cursor_id,
                agent_filters=build_access_filters(identity, Agent),
                user_filters=build_access_filters(identity, User),
            )

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        views = [_to_view(row) for row in rows]

        next_cursor: str | None = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor(last.created_at, last.id)

        return Page(data=views, has_more=has_more, next_cursor=next_cursor)

    async def lookup(self, ids: Sequence[str], *, identity: Identity) -> list[ActorView]:
        """Resolve the given actor ids to display fields, scoped to the caller.

        Only actors the caller can already see are resolved. Any other id is
        left out exactly like an unknown id, so the response does not reveal
        whether it exists.

        - Agents: the caller's agent visibility
          (``build_access_filters(identity, Agent)``), the same set
          ``GET /agents/{id}`` answers for.
        - Users (``build_lookup_user_filters``): the caller itself, the owner
          or approver of an agent it can see, the actor of an event or audit
          entry it can see, and the creator of a credential it can read
          (shared credentials included, through the credential read scoping).

        ``org:admin`` resolves every actor. ``users:read`` does not widen
        this: it reads only the caller's own user row elsewhere too. There is
        no listing or search. Cost: one directory query, plus one credential
        read for the creator relation, each bounded by ``ids``.
        """
        unique_ids = list(dict.fromkeys(ids))
        if not unique_ids:
            return []
        creator_ids = await self._visible_credential_creators(unique_ids, identity=identity)
        agent_filters = build_access_filters(identity, Agent)
        user_filters = build_lookup_user_filters(identity, extra_user_ids=creator_ids)
        async with self._ctx.admin_db.session() as session:
            rows = await ActorDirectoryRepository.get_by_ids(
                session, unique_ids, agent_filters=agent_filters, user_filters=user_filters
            )
        return [_to_view(row) for row in rows]

    async def _visible_credential_creators(
        self, ids: Sequence[str], *, identity: Identity
    ) -> set[str]:
        """The ids in ``ids`` that created a credential the caller can read.

        ``org:admin`` already resolves every user, and the caller always
        resolves itself, so neither needs the credential query.
        """
        if self._credential_creators is None or ORG_ADMIN in identity.permissions:
            return set()
        candidates = [i for i in ids if i != identity.sub]
        if not candidates:
            return set()
        return await self._credential_creators.visible_creator_ids(candidates, identity=identity)


def _to_view(row: Any) -> ActorView:
    return ActorView(
        id=row.id,
        actor_type=ActorType(row.actor_type),
        name=row.name,
        active=bool(row.active),
        created_at=row.created_at,
    )
