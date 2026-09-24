"""Unit tests for AgentService.update_agent()."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.auth.services.agent_service import AgentService
from jentic_one.auth.services.errors import (
    ActorNotFoundError,
    InvalidOwnerError,
    InvalidTransitionError,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.db import DatabaseIntegrityError


def _admin_identity() -> Identity:
    return Identity(
        sub="usr_admin",
        email="admin@example.com",
        permissions=["agents:write", "org:admin"],
    )


def _make_ctx() -> MagicMock:
    ctx = MagicMock()
    mock_session = AsyncMock()
    ctx.admin_db.transaction.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.admin_db.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_agent_row(
    *,
    status: str = "active",
    owner_id: str | None = None,
    name: str = "my-agent",
    description: str | None = None,
) -> MagicMock:
    row = MagicMock()
    row.id = "agnt_test123"
    row.status = status
    row.owner_id = owner_id
    row.name = name
    row.description = description
    row.registered_by = "self"
    row.parent_agent_id = None
    row.approved_by = None
    row.denial_reason = None
    row.denied_by = None
    row.created_at = datetime(2026, 6, 23, tzinfo=UTC)
    row.approved_at = None
    row.has_api_key = False
    return row


@pytest.mark.asyncio
@patch(
    "jentic_one.auth.services.agent_service.revoke_active_grants_for_agent",
    new_callable=AsyncMock,
)
@patch("jentic_one.auth.services.agent_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.auth.services.agent_service.AgentRepository")
async def test_update_agent_sets_owner(
    mock_repo: MagicMock,
    mock_audit: AsyncMock,
    mock_revoke: AsyncMock,
) -> None:
    ctx = _make_ctx()
    svc = AgentService(ctx)

    agent_before = _make_agent_row(owner_id=None)
    agent_after = _make_agent_row(owner_id="usr_new_owner")
    mock_repo.get_by_id_for_update = AsyncMock(return_value=agent_before)
    mock_repo.update_agent = AsyncMock(return_value=agent_after)

    identity = _admin_identity()
    view = await svc.update_agent(
        "agnt_test123",
        update_data={"owner_id": "usr_new_owner"},
        identity=identity,
    )

    assert view.owner_id == "usr_new_owner"
    mock_repo.update_agent.assert_called_once()
    mock_audit.assert_called_once()
    # An owner change is a transfer — the G10 (#1222) grant sweep runs in the
    # same transaction, attributed to the transferring actor.
    mock_revoke.assert_awaited_once()
    assert mock_revoke.await_args is not None
    assert mock_revoke.await_args.args[1] == "agnt_test123"
    assert mock_revoke.await_args.kwargs["identity"] is identity


@pytest.mark.asyncio
@patch(
    "jentic_one.auth.services.agent_service.revoke_active_grants_for_agent",
    new_callable=AsyncMock,
)
@patch("jentic_one.auth.services.agent_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.auth.services.agent_service.AgentRepository")
async def test_update_agent_unchanged_owner_skips_grant_sweep(
    mock_repo: MagicMock,
    mock_audit: AsyncMock,
    mock_revoke: AsyncMock,
) -> None:
    """A PATCH carrying the CURRENT owner_id is not a transfer — no sweep."""
    ctx = _make_ctx()
    svc = AgentService(ctx)

    same_owner = _make_agent_row(owner_id="usr_owner")
    mock_repo.get_by_id_for_update = AsyncMock(return_value=same_owner)
    mock_repo.update_agent = AsyncMock(return_value=same_owner)

    await svc.update_agent(
        "agnt_test123",
        update_data={"owner_id": "usr_owner"},
        identity=_admin_identity(),
    )

    mock_revoke.assert_not_awaited()


@pytest.mark.asyncio
@patch(
    "jentic_one.auth.services.agent_service.revoke_active_grants_for_agent",
    new_callable=AsyncMock,
)
@patch("jentic_one.auth.services.agent_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.auth.services.agent_service.AgentRepository")
async def test_update_agent_grant_sweep_failure_fails_transfer(
    mock_repo: MagicMock,
    mock_audit: AsyncMock,
    mock_revoke: AsyncMock,
) -> None:
    """The sweep is NOT best-effort: its failure propagates (and, in the real
    transaction context manager, rolls the owner write back) rather than
    leaving a transferred agent with live grants."""
    ctx = _make_ctx()
    svc = AgentService(ctx)

    mock_repo.get_by_id_for_update = AsyncMock(return_value=_make_agent_row(owner_id="usr_old"))
    mock_repo.update_agent = AsyncMock(return_value=_make_agent_row(owner_id="usr_new"))
    mock_revoke.side_effect = RuntimeError("sweep failed")

    with pytest.raises(RuntimeError, match="sweep failed"):
        await svc.update_agent(
            "agnt_test123",
            update_data={"owner_id": "usr_new"},
            identity=_admin_identity(),
        )
    mock_audit.assert_not_called()


@pytest.mark.asyncio
@patch(
    "jentic_one.auth.services.agent_service.revoke_active_grants_for_agent",
    new_callable=AsyncMock,
)
@patch("jentic_one.auth.services.agent_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.auth.services.agent_service.AgentRepository")
async def test_update_agent_sets_name_and_description(
    mock_repo: MagicMock,
    mock_audit: AsyncMock,
    mock_revoke: AsyncMock,
) -> None:
    ctx = _make_ctx()
    svc = AgentService(ctx)

    agent_before = _make_agent_row(name="old-name", description="old desc")
    agent_after = _make_agent_row(name="new-name", description="new desc")
    mock_repo.get_by_id_for_update = AsyncMock(return_value=agent_before)
    mock_repo.update_agent = AsyncMock(return_value=agent_after)

    view = await svc.update_agent(
        "agnt_test123",
        update_data={"name": "new-name", "description": "new desc"},
        identity=_admin_identity(),
    )

    assert view.name == "new-name"
    assert view.description == "new desc"
    # No owner change — the grant sweep must not run.
    mock_revoke.assert_not_awaited()


@pytest.mark.asyncio
@patch("jentic_one.auth.services.agent_service.AgentRepository")
async def test_update_agent_not_found(mock_repo: MagicMock) -> None:
    ctx = _make_ctx()
    svc = AgentService(ctx)
    mock_repo.get_by_id_for_update = AsyncMock(return_value=None)

    with pytest.raises(ActorNotFoundError):
        await svc.update_agent(
            "agnt_missing",
            update_data={"name": "x"},
            identity=_admin_identity(),
        )


@pytest.mark.asyncio
@patch("jentic_one.auth.services.agent_service.AgentRepository")
async def test_update_agent_archived_raises(mock_repo: MagicMock) -> None:
    ctx = _make_ctx()
    svc = AgentService(ctx)
    mock_repo.get_by_id_for_update = AsyncMock(return_value=_make_agent_row(status="archived"))

    with pytest.raises(InvalidTransitionError):
        await svc.update_agent(
            "agnt_test123",
            update_data={"name": "x"},
            identity=_admin_identity(),
        )


@pytest.mark.asyncio
@patch(
    "jentic_one.auth.services.agent_service.revoke_active_grants_for_agent",
    new_callable=AsyncMock,
)
@patch("jentic_one.auth.services.agent_service.record_audit", new_callable=AsyncMock)
@patch("jentic_one.auth.services.agent_service.AgentRepository")
async def test_update_agent_invalid_owner_raises(
    mock_repo: MagicMock,
    mock_audit: AsyncMock,
    mock_revoke: AsyncMock,
) -> None:
    ctx = _make_ctx()
    ctx.admin_db.transaction.return_value.__aexit__ = AsyncMock(
        side_effect=DatabaseIntegrityError("FK violation on owner_id")
    )
    svc = AgentService(ctx)

    mock_repo.get_by_id_for_update = AsyncMock(return_value=_make_agent_row())
    mock_repo.update_agent = AsyncMock(return_value=_make_agent_row())

    with pytest.raises(InvalidOwnerError):
        await svc.update_agent(
            "agnt_test123",
            update_data={"owner_id": "usr_nonexistent"},
            identity=_admin_identity(),
        )
