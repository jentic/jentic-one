"""Unit tests for AgentService.claim() — the DCR ownership-claim primitive."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.auth.services.agent_service import AgentService
from jentic_one.auth.services.errors import (
    ActorNotFoundError,
    AgentAlreadyOwnedError,
    ClaimActorNotAllowedError,
    ClaimTokenInvalidError,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models import ActorType

_TOKEN = "claim-secret-xyz"
_TOKEN_HASH = hashlib.sha256(_TOKEN.encode()).hexdigest()


def _member_identity() -> Identity:
    """A plain member with NO agent permissions — the token is the proof."""
    return Identity(sub="usr_member", email="member@example.com", permissions=[])


def _make_ctx() -> MagicMock:
    ctx = MagicMock()
    mock_session = AsyncMock()
    ctx.admin_db.transaction.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.admin_db.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _pending_row(
    *,
    owner_id: str | None = None,
    claim_token_hash: str | None = _TOKEN_HASH,
    claim_expires_at: datetime | None = None,
    status: str = "pending",
) -> MagicMock:
    row = MagicMock()
    row.id = "agnt_test123"
    row.status = status
    row.owner_id = owner_id
    row.name = "my-agent"
    row.description = None
    row.registered_by = "self"
    row.parent_agent_id = None
    row.approved_by = None
    row.denial_reason = None
    row.denied_by = None
    row.created_at = datetime(2026, 6, 23, tzinfo=UTC)
    row.approved_at = None
    row.has_api_key = False
    row.claim_token_hash = claim_token_hash
    row.claim_expires_at = claim_expires_at or (datetime.now(UTC) + timedelta(minutes=10))
    return row


@pytest.mark.asyncio
@patch("jentic_one.auth.services.agent_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.auth.services.agent_service.AgentRepository")
async def test_claim_sets_owner_and_consumes_token(
    mock_repo: MagicMock, mock_audit: AsyncMock
) -> None:
    """A member with a valid token becomes the owner and the token is cleared."""
    ctx = _make_ctx()
    svc = AgentService(ctx)
    row = _pending_row()
    mock_repo.get_by_id_for_update = AsyncMock(return_value=row)

    async def _set_owner(_session: object, agent: MagicMock, *, owner_id: str) -> MagicMock:
        agent.owner_id = owner_id
        agent.claim_token_hash = None
        agent.claim_expires_at = None
        return agent

    mock_repo.set_owner_from_claim = AsyncMock(side_effect=_set_owner)

    view = await svc.claim("agnt_test123", token=_TOKEN, identity=_member_identity())

    assert view.owner_id == "usr_member"
    mock_repo.set_owner_from_claim.assert_awaited_once()
    mock_audit.assert_awaited_once()


@pytest.mark.asyncio
@patch("jentic_one.auth.services.agent_service.AgentRepository")
async def test_claim_unknown_agent_raises(mock_repo: MagicMock) -> None:
    ctx = _make_ctx()
    svc = AgentService(ctx)
    mock_repo.get_by_id_for_update = AsyncMock(return_value=None)

    with pytest.raises(ActorNotFoundError):
        await svc.claim("agnt_missing", token=_TOKEN, identity=_member_identity())


@pytest.mark.asyncio
@patch("jentic_one.auth.services.agent_service.AgentRepository")
async def test_claim_already_owned_raises(mock_repo: MagicMock) -> None:
    ctx = _make_ctx()
    svc = AgentService(ctx)
    mock_repo.get_by_id_for_update = AsyncMock(return_value=_pending_row(owner_id="usr_other"))

    with pytest.raises(AgentAlreadyOwnedError):
        await svc.claim("agnt_test123", token=_TOKEN, identity=_member_identity())


@pytest.mark.asyncio
@patch("jentic_one.auth.services.agent_service.AgentRepository")
async def test_claim_wrong_token_raises(mock_repo: MagicMock) -> None:
    ctx = _make_ctx()
    svc = AgentService(ctx)
    mock_repo.get_by_id_for_update = AsyncMock(return_value=_pending_row())

    with pytest.raises(ClaimTokenInvalidError):
        await svc.claim("agnt_test123", token="wrong-token", identity=_member_identity())


@pytest.mark.asyncio
@patch("jentic_one.auth.services.agent_service.AgentRepository")
async def test_claim_no_token_issued_raises(mock_repo: MagicMock) -> None:
    """An agent that never had a claim token minted is not claimable — and we
    surface the same error as a mismatch so claimability isn't leaked."""
    ctx = _make_ctx()
    svc = AgentService(ctx)
    mock_repo.get_by_id_for_update = AsyncMock(return_value=_pending_row(claim_token_hash=None))

    with pytest.raises(ClaimTokenInvalidError):
        await svc.claim("agnt_test123", token=_TOKEN, identity=_member_identity())


@pytest.mark.asyncio
@patch("jentic_one.auth.services.agent_service.AgentRepository")
async def test_claim_expired_token_raises(mock_repo: MagicMock) -> None:
    ctx = _make_ctx()
    svc = AgentService(ctx)
    mock_repo.get_by_id_for_update = AsyncMock(
        return_value=_pending_row(claim_expires_at=datetime.now(UTC) - timedelta(minutes=1))
    )

    with pytest.raises(ClaimTokenInvalidError, match="expired"):
        await svc.claim("agnt_test123", token=_TOKEN, identity=_member_identity())


@pytest.mark.asyncio
@patch("jentic_one.auth.services.agent_service.AgentRepository")
async def test_claim_archived_agent_raises(mock_repo: MagicMock) -> None:
    ctx = _make_ctx()
    svc = AgentService(ctx)
    mock_repo.get_by_id_for_update = AsyncMock(return_value=_pending_row(status="archived"))

    with pytest.raises(ActorNotFoundError):
        await svc.claim("agnt_test123", token=_TOKEN, identity=_member_identity())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor_type",
    [ActorType.AGENT, ActorType.SERVICE_ACCOUNT, ActorType.TOOLKIT],
)
@patch("jentic_one.auth.services.agent_service.AgentRepository")
async def test_claim_non_user_actor_rejected(mock_repo: MagicMock, actor_type: ActorType) -> None:
    """Only human users can own agents (owner_id FK → users.id).

    A non-user actor presenting the token is rejected up front (403) — before any
    DB read — so it can never write a non-user id into the users-FK column (which
    would be an unhandled integrity error / 500).
    """
    ctx = _make_ctx()
    svc = AgentService(ctx)
    mock_repo.get_by_id_for_update = AsyncMock()
    identity = Identity(sub="agnt_bot", email="", permissions=[], actor_type=actor_type)

    with pytest.raises(ClaimActorNotAllowedError):
        await svc.claim("agnt_test123", token=_TOKEN, identity=identity)

    # Guard fires before any repo access — no row is ever locked/read.
    mock_repo.get_by_id_for_update.assert_not_awaited()
