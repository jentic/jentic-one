"""Unit tests for cross-request item manipulation guard."""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jentic_one.control.services.access_requests.errors import ItemNotOnRequestError
from jentic_one.control.services.access_requests.schemas.effects import SkippedEffect
from jentic_one.control.services.access_requests.service import AccessRequestService
from jentic_one.shared.auth.identity import Identity


def _identity(sub: str = "reviewer_1", permissions: list[str] | None = None) -> Identity:
    return Identity(
        sub=sub,
        email="test@example.com",
        permissions=permissions or ["agents:write"],
        parent_actor_id=None,
    )


def _mock_request(
    request_id: str = "req_1",
    item_ids: list[str] | None = None,
    created_by: str = "filer_1",
    filer_owner_id: str = "reviewer_1",
) -> MagicMock:
    request = MagicMock()
    request.id = request_id
    request.status = "pending"
    request.created_by = created_by
    request.filer_owner_id = filer_owner_id
    request.expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(days=7)
    request.filed_at = dt.datetime.now(dt.UTC)
    request.actor_id = "actor_1"
    request.reason = "testing"
    request.requested_by = "tester"
    request.approve_url = "http://example.com"
    items = []
    for iid in item_ids or ["item_1", "item_2"]:
        item = MagicMock()
        item.id = iid
        # Items inherit the request's actor at creation; decide() asserts that
        # invariant on the item it is about to approve (defense-in-depth).
        item.actor_id = "actor_1"
        item.resource_type = "credential"
        item.action = "read"
        item.resource_id = None
        item.resource_reference = None
        item.to_type = None
        item.to_id = None
        item.rules = None
        # A bare MagicMock attribute would fail the view's `str | None`
        # validation (and read as a policy carrier); pin the Phase-3 column.
        item.rule_set_id = None
        item.status = "pending"
        item.applied_effects = None
        item.decided_by = None
        item.decided_at = None
        item.decision_reason = None
        items.append(item)
    request.items = items
    return request


def _service() -> AccessRequestService:
    ctx = MagicMock()
    ctx.control_db.transaction.return_value = AsyncMock()
    ctx.control_db.session.return_value = AsyncMock()
    return AccessRequestService(ctx)


@pytest.mark.asyncio
@patch("jentic_one.control.services.access_requests.service.AccessRequestRepository")
@patch("jentic_one.control.services.access_requests.service.build_access_filters")
async def test_decide_rejects_foreign_item(mock_filters: MagicMock, mock_repo: MagicMock) -> None:
    mock_filters.return_value = []
    request = _mock_request(item_ids=["item_1", "item_2"])
    mock_repo.get = AsyncMock(return_value=request)

    svc = _service()
    identity = _identity(sub="reviewer_1")

    with pytest.raises(ItemNotOnRequestError) as exc_info:
        await svc.decide(
            "req_1",
            identity=identity,
            item_decisions=[{"item_id": "foreign_item", "decision": "approved"}],
        )

    assert exc_info.value.item_id == "foreign_item"
    assert exc_info.value.request_id == "req_1"


@pytest.mark.asyncio
@patch("jentic_one.control.services.access_requests.service.AccessRequestRepository")
@patch("jentic_one.control.services.access_requests.service.build_access_filters")
async def test_amend_rejects_foreign_item(mock_filters: MagicMock, mock_repo: MagicMock) -> None:
    mock_filters.return_value = []
    request = _mock_request(item_ids=["item_1", "item_2"])
    mock_repo.get = AsyncMock(return_value=request)

    svc = _service()
    identity = _identity(sub="reviewer_1")

    with pytest.raises(ItemNotOnRequestError) as exc_info:
        await svc.amend(
            "req_1",
            identity=identity,
            item_amendments=[{"item_id": "foreign_item", "rules": {"new": "rule"}}],
        )

    assert exc_info.value.item_id == "foreign_item"
    assert exc_info.value.request_id == "req_1"


@pytest.mark.asyncio
@patch("jentic_one.control.services.access_requests.service.AccessRequestRepository")
@patch("jentic_one.control.services.access_requests.service.build_access_filters")
@patch("jentic_one.control.services.access_requests.service.EffectApplicator")
async def test_decide_allows_valid_item(
    mock_effect_cls: MagicMock, mock_filters: MagicMock, mock_repo: MagicMock
) -> None:
    mock_filters.return_value = []
    request = _mock_request(item_ids=["item_1", "item_2"])
    refreshed = _mock_request(item_ids=["item_1", "item_2"])
    mock_repo.get = AsyncMock(side_effect=[request, refreshed])
    # decide() asserts the item it is about to approve belongs to the request's
    # own actor (defense-in-depth against approving a grant for a foreign actor);
    # _mock_request seeds each item with that actor_id, mirroring production.
    decided_item = MagicMock()
    decided_item.actor_id = request.actor_id
    mock_repo.decide_item = AsyncMock(return_value=decided_item)
    mock_repo.set_applied_effects = AsyncMock()
    mock_repo.list_unacked_admin_effect_items = AsyncMock(return_value=[])
    mock_effect_cls.return_value.apply = AsyncMock(return_value=SkippedEffect(reason="test"))
    mock_effect_cls.return_value.validate = AsyncMock(return_value=None)

    svc = _service()
    identity = _identity(sub="reviewer_1")

    result = await svc.decide(
        "req_1",
        identity=identity,
        item_decisions=[{"item_id": "item_1", "decision": "approved"}],
    )

    assert result is not None
    mock_repo.decide_item.assert_called_once()
