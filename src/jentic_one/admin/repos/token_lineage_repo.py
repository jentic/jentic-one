"""Repository for paired AccessToken and RefreshToken revocation and deletion sweeps."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import NamedTuple

from sqlalchemy import ColumnElement, delete, or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from jentic_one.admin.core.schema.access_tokens import AccessToken
from jentic_one.admin.core.schema.refresh_tokens import RefreshToken


class TokenSweep(NamedTuple):
    """Row counts swept across the access and refresh token tables."""

    access: int
    refresh: int


async def _revoke_live(
    session: AsyncSession,
    model: type[AccessToken] | type[RefreshToken],
    *predicates: ColumnElement[bool],
) -> int:
    stmt = (
        update(model)
        .where(*predicates)
        .where(model.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    result = await session.execute(stmt)
    await session.flush()
    return int(result.rowcount)  # type: ignore[attr-defined]


async def _delete(
    session: AsyncSession,
    model: type[AccessToken] | type[RefreshToken],
    predicate: ColumnElement[bool],
) -> int:
    result = await session.execute(delete(model).where(predicate))
    await session.flush()
    return int(result.rowcount)  # type: ignore[attr-defined]


def _actor_or_grants_predicate(
    model: type[AccessToken] | type[RefreshToken],
    actor_id: str,
    grant_ids: Sequence[str],
) -> ColumnElement[bool]:
    predicate: ColumnElement[bool] = model.actor_id == actor_id
    if grant_ids:
        predicate = or_(predicate, model.oauth_grant_id.in_(list(grant_ids)))
    return predicate


class TokenLineageRepository:
    """Revokes/deletes access AND refresh tokens together — flush-only."""

    # Refresh first: matches token_service, which already holds a refresh-row FOR UPDATE lock.
    _MODELS = (RefreshToken, AccessToken)

    @staticmethod
    async def revoke_family(session: AsyncSession, token_family_id: str) -> TokenSweep:
        """Revoke every live refresh and access token in ``token_family_id``."""
        refresh_model, access_model = TokenLineageRepository._MODELS
        refresh = await _revoke_live(
            session, refresh_model, refresh_model.token_family_id == token_family_id
        )
        access = await _revoke_live(
            session, access_model, access_model.token_family_id == token_family_id
        )
        return TokenSweep(access=access, refresh=refresh)

    @staticmethod
    async def revoke_for_actor(
        session: AsyncSession, actor_id: str, *, actor_type: str
    ) -> TokenSweep:
        """Revoke every live refresh and access token held by ``(actor_id, actor_type)``."""
        refresh_model, access_model = TokenLineageRepository._MODELS
        refresh = await _revoke_live(
            session,
            refresh_model,
            refresh_model.actor_id == actor_id,
            refresh_model.actor_type == actor_type,
        )
        access = await _revoke_live(
            session,
            access_model,
            access_model.actor_id == actor_id,
            access_model.actor_type == actor_type,
        )
        return TokenSweep(access=access, refresh=refresh)

    @staticmethod
    async def revoke_by_grant(session: AsyncSession, oauth_grant_id: str) -> TokenSweep:
        """Revoke every live refresh and access token minted under one consent grant."""
        refresh_model, access_model = TokenLineageRepository._MODELS
        refresh = await _revoke_live(
            session, refresh_model, refresh_model.oauth_grant_id == oauth_grant_id
        )
        access = await _revoke_live(
            session, access_model, access_model.oauth_grant_id == oauth_grant_id
        )
        return TokenSweep(access=access, refresh=refresh)

    @staticmethod
    async def revoke_by_client(session: AsyncSession, oauth_client_id: str) -> TokenSweep:
        """Revoke every live refresh and access token carrying one client's lineage."""
        refresh_model, access_model = TokenLineageRepository._MODELS
        refresh = await _revoke_live(
            session, refresh_model, refresh_model.oauth_client_id == oauth_client_id
        )
        access = await _revoke_live(
            session, access_model, access_model.oauth_client_id == oauth_client_id
        )
        return TokenSweep(access=access, refresh=refresh)

    @staticmethod
    async def delete_for_actor(
        session: AsyncSession, actor_id: str, *, grant_ids: Sequence[str] = ()
    ) -> TokenSweep:
        """Delete every refresh and access token held by ``actor_id`` or under ``grant_ids``."""
        refresh_model, access_model = TokenLineageRepository._MODELS
        refresh = await _delete(
            session,
            refresh_model,
            _actor_or_grants_predicate(refresh_model, actor_id, grant_ids),
        )
        access = await _delete(
            session,
            access_model,
            _actor_or_grants_predicate(access_model, actor_id, grant_ids),
        )
        return TokenSweep(access=access, refresh=refresh)

    @staticmethod
    async def delete_expired(session: AsyncSession, before: datetime) -> TokenSweep:
        """Delete every refresh and access token with ``expires_at < before``."""
        refresh_model, access_model = TokenLineageRepository._MODELS
        refresh = await _delete(session, refresh_model, refresh_model.expires_at < before)
        access = await _delete(session, access_model, access_model.expires_at < before)
        return TokenSweep(access=access, refresh=refresh)
