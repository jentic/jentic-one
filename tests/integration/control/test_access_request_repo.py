"""Integration tests for AccessRequestRepository — full lifecycle with real DB."""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete

from jentic_one.control.core.schema.access_request_items import AccessRequestItem
from jentic_one.control.core.schema.access_requests import AccessRequest
from jentic_one.control.repos.access_request_repo import AccessRequestRepository
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models.access_requests import AccessRequestItemStatus, AccessRequestStatus

pytestmark = pytest.mark.integration


@pytest.fixture()
async def clean_access_requests(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Ensure access_request tables are empty before and after each test."""
    async with control_db.session() as session:
        await session.execute(delete(AccessRequestItem))
        await session.execute(delete(AccessRequest))
        await session.commit()
    yield
    async with control_db.session() as session:
        await session.execute(delete(AccessRequestItem))
        await session.execute(delete(AccessRequest))
        await session.commit()


def _make_items() -> list[dict[str, object]]:
    return [
        {
            "resource_type": "api",
            "action": "invoke",
            "resource_id": "res_123",
            "to_type": "toolkit",
            "to_id": "tk_abc",
        },
    ]


@pytest.fixture()
def expires_at() -> dt.datetime:
    return dt.datetime.now(dt.UTC) + dt.timedelta(days=7)


async def test_create_request_with_items(
    control_db: DatabaseSession,
    clean_access_requests: None,
    expires_at: dt.datetime,
) -> None:
    async with control_db.session() as session:
        request = await AccessRequestRepository.create(
            session,
            actor_id="actor_1",
            reason="Need access",
            requested_by="user@example.com",
            approve_url="https://example.com/approve",
            expires_at=expires_at,
            items=_make_items(),
            created_by="user_1",
        )
        assert request.id.startswith("areq_")
        assert request.status == AccessRequestStatus.PENDING
        assert len(request.items) == 1
        assert request.items[0].status == AccessRequestItemStatus.PENDING
        assert request.items[0].id.startswith("arqi_")
        await session.commit()


async def test_create_default_rules_applied_when_missing(
    control_db: DatabaseSession,
    clean_access_requests: None,
    expires_at: dt.datetime,
) -> None:
    # The read-only default is substituted only for rule-bearing item types
    # (credential:bind); other types carry no enforceable rules — see
    # RULE_BEARING_COMBINATIONS.
    async with control_db.session() as session:
        request = await AccessRequestRepository.create(
            session,
            actor_id="actor_1",
            reason=None,
            requested_by="agent@example.com",
            approve_url="https://example.com/approve",
            expires_at=expires_at,
            items=[
                {
                    "resource_type": "credential",
                    "action": "bind",
                    "resource_id": "cred_1",
                    "to_type": "toolkit",
                    "to_id": "tk_1",
                }
            ],
            created_by="user_1",
        )
        assert request.items[0].rules == [{"effect": "allow", "methods": ["GET"]}]
        await session.commit()


async def test_create_no_default_rules_for_non_rule_bearing_item(
    control_db: DatabaseSession,
    clean_access_requests: None,
    expires_at: dt.datetime,
) -> None:
    # A toolkit:bind (agent↔toolkit) has no credential to key rules on, so the
    # repo must NOT stamp a non-enforceable default allowlist onto it.
    async with control_db.session() as session:
        request = await AccessRequestRepository.create(
            session,
            actor_id="actor_1",
            reason=None,
            requested_by="agent@example.com",
            approve_url="https://example.com/approve",
            expires_at=expires_at,
            items=[{"resource_type": "toolkit", "action": "bind", "resource_id": "tk_1"}],
            created_by="user_1",
        )
        assert request.items[0].rules is None
        await session.commit()


async def test_list_all_returns_paginated_results(
    control_db: DatabaseSession,
    clean_access_requests: None,
    expires_at: dt.datetime,
) -> None:
    async with control_db.session() as session:
        for i in range(3):
            await AccessRequestRepository.create(
                session,
                actor_id="actor_1",
                reason=f"Request {i}",
                requested_by="user@example.com",
                approve_url="https://example.com/approve",
                expires_at=expires_at,
                items=[
                    {
                        "resource_type": "api",
                        "action": "invoke",
                        "resource_id": f"res_{i}",
                        "to_type": "toolkit",
                        "to_id": "tk_abc",
                    }
                ],
                created_by="user_1",
            )
        results = await AccessRequestRepository.list_all(session, limit=2)
        assert len(results) == 3  # limit+1 fetch
        await session.commit()


async def test_list_all_filters_by_actor_id(
    control_db: DatabaseSession,
    clean_access_requests: None,
    expires_at: dt.datetime,
) -> None:
    async with control_db.session() as session:
        await AccessRequestRepository.create(
            session,
            actor_id="actor_a",
            reason=None,
            requested_by="user@example.com",
            approve_url="https://example.com/approve",
            expires_at=expires_at,
            items=_make_items(),
            created_by="user_1",
        )
        await AccessRequestRepository.create(
            session,
            actor_id="actor_b",
            reason=None,
            requested_by="user@example.com",
            approve_url="https://example.com/approve",
            expires_at=expires_at,
            items=_make_items(),
            created_by="user_2",
        )
        results = await AccessRequestRepository.list_all(session, actor_id="actor_a")
        assert len(results) == 1
        assert results[0].actor_id == "actor_a"
        await session.commit()


async def test_withdraw_sets_status(
    control_db: DatabaseSession,
    clean_access_requests: None,
    expires_at: dt.datetime,
) -> None:
    async with control_db.session() as session:
        request = await AccessRequestRepository.create(
            session,
            actor_id="actor_1",
            reason=None,
            requested_by="user@example.com",
            approve_url="https://example.com/approve",
            expires_at=expires_at,
            items=_make_items(),
            created_by="user_1",
        )
        withdrawn = await AccessRequestRepository.withdraw(session, request.id)
        assert withdrawn is not None
        assert withdrawn.status == AccessRequestStatus.WITHDRAWN
        assert all(item.status == AccessRequestItemStatus.WITHDRAWN for item in withdrawn.items)
        await session.commit()


async def test_withdraw_nonexistent_returns_none(
    control_db: DatabaseSession, clean_access_requests: None
) -> None:
    async with control_db.session() as session:
        result = await AccessRequestRepository.withdraw(session, "areq_nonexistent")
        assert result is None


async def test_decide_item_approve_single(
    control_db: DatabaseSession,
    clean_access_requests: None,
    expires_at: dt.datetime,
) -> None:
    async with control_db.session() as session:
        request = await AccessRequestRepository.create(
            session,
            actor_id="actor_1",
            reason=None,
            requested_by="user@example.com",
            approve_url="https://example.com/approve",
            expires_at=expires_at,
            items=_make_items(),
            created_by="user_1",
        )
        item = await AccessRequestRepository.decide_item(
            session,
            request.items[0].id,
            AccessRequestItemStatus.APPROVED,
            decided_by="admin_1",
            decision_reason="Looks good",
        )
        assert item is not None
        assert item.status == AccessRequestItemStatus.APPROVED
        assert item.decided_by == "admin_1"
        assert item.decided_at is not None
        refreshed = await AccessRequestRepository.get(session, request.id)
        assert refreshed is not None
        assert refreshed.status == AccessRequestStatus.APPROVED
        await session.commit()


async def test_decide_item_non_pending_returns_none(
    control_db: DatabaseSession,
    clean_access_requests: None,
    expires_at: dt.datetime,
) -> None:
    async with control_db.session() as session:
        request = await AccessRequestRepository.create(
            session,
            actor_id="actor_1",
            reason=None,
            requested_by="user@example.com",
            approve_url="https://example.com/approve",
            expires_at=expires_at,
            items=_make_items(),
            created_by="user_1",
        )
        await AccessRequestRepository.decide_item(
            session,
            request.items[0].id,
            AccessRequestItemStatus.APPROVED,
            decided_by="admin_1",
        )
        result = await AccessRequestRepository.decide_item(
            session,
            request.items[0].id,
            AccessRequestItemStatus.DENIED,
            decided_by="admin_2",
        )
        assert result is None
        await session.commit()


async def test_find_pending_duplicate_finds_existing(
    control_db: DatabaseSession,
    clean_access_requests: None,
    expires_at: dt.datetime,
) -> None:
    async with control_db.session() as session:
        await AccessRequestRepository.create(
            session,
            actor_id="actor_1",
            reason=None,
            requested_by="user@example.com",
            approve_url="https://example.com/approve",
            expires_at=expires_at,
            items=_make_items(),
            created_by="user_1",
        )
        dup = await AccessRequestRepository.find_pending_duplicate(
            session,
            actor_id="actor_1",
            resource_type="api",
            action="invoke",
            to_id="tk_abc",
            resource_id="res_123",
        )
        assert dup is not None
        assert dup.actor_id == "actor_1"
        await session.commit()


async def test_find_pending_duplicate_no_match_different_resource(
    control_db: DatabaseSession,
    clean_access_requests: None,
    expires_at: dt.datetime,
) -> None:
    async with control_db.session() as session:
        await AccessRequestRepository.create(
            session,
            actor_id="actor_1",
            reason=None,
            requested_by="user@example.com",
            approve_url="https://example.com/approve",
            expires_at=expires_at,
            items=_make_items(),
            created_by="user_1",
        )
        dup = await AccessRequestRepository.find_pending_duplicate(
            session,
            actor_id="actor_1",
            resource_type="api",
            action="invoke",
            to_id="tk_abc",
            resource_id="res_999",
        )
        assert dup is None
        await session.commit()


async def test_find_pending_duplicate_null_to_id(
    control_db: DatabaseSession,
    clean_access_requests: None,
    expires_at: dt.datetime,
) -> None:
    async with control_db.session() as session:
        await AccessRequestRepository.create(
            session,
            actor_id="actor_1",
            reason=None,
            requested_by="user@example.com",
            approve_url="https://example.com/approve",
            expires_at=expires_at,
            items=[{"resource_type": "api", "action": "invoke", "resource_id": "res_1"}],
            created_by="user_1",
        )
        dup = await AccessRequestRepository.find_pending_duplicate(
            session,
            actor_id="actor_1",
            resource_type="api",
            action="invoke",
            to_id=None,
            resource_id="res_1",
        )
        assert dup is not None
        await session.commit()


async def test_find_pending_duplicate_reference_distinguishes_toolkit(
    control_db: DatabaseSession,
    clean_access_requests: None,
    expires_at: dt.datetime,
) -> None:
    """A reference-only item (NULL resource_id/to_id) must dedup on the
    vendor/name reference, so stripe.com is *not* a duplicate of github.com."""
    async with control_db.session() as session:
        await AccessRequestRepository.create(
            session,
            actor_id="actor_1",
            reason=None,
            requested_by="user@example.com",
            approve_url="https://example.com/approve",
            expires_at=expires_at,
            items=[
                {
                    "resource_type": "toolkit",
                    "action": "bind",
                    "resource_reference": {"vendor": "github.com", "name": "repos"},
                }
            ],
            created_by="user_1",
        )

        # A different vendor must NOT be flagged as a duplicate.
        other = await AccessRequestRepository.find_pending_duplicate(
            session,
            actor_id="actor_1",
            resource_type="toolkit",
            action="bind",
            to_id=None,
            resource_id=None,
            resource_reference={"vendor": "stripe.com", "name": "repos"},
        )
        assert other is None

        # The same vendor/name (modulo case + inert extra keys) IS a duplicate.
        same = await AccessRequestRepository.find_pending_duplicate(
            session,
            actor_id="actor_1",
            resource_type="toolkit",
            action="bind",
            to_id=None,
            resource_id=None,
            resource_reference={"vendor": "GitHub.com", "name": "repos", "ignored": "x"},
        )
        assert same is not None
        await session.commit()


async def test_amend_item_rules(
    control_db: DatabaseSession,
    clean_access_requests: None,
    expires_at: dt.datetime,
) -> None:
    async with control_db.session() as session:
        request = await AccessRequestRepository.create(
            session,
            actor_id="actor_1",
            reason=None,
            requested_by="user@example.com",
            approve_url="https://example.com/approve",
            expires_at=expires_at,
            items=_make_items(),
            created_by="user_1",
        )
        new_rules = [{"effect": "allow", "methods": ["GET", "POST"]}]
        item = await AccessRequestRepository.amend_item(
            session, request.items[0].id, rules=new_rules
        )
        assert item is not None
        assert item.rules == new_rules
        await session.commit()


async def test_amend_item_non_pending_returns_none(
    control_db: DatabaseSession,
    clean_access_requests: None,
    expires_at: dt.datetime,
) -> None:
    async with control_db.session() as session:
        request = await AccessRequestRepository.create(
            session,
            actor_id="actor_1",
            reason=None,
            requested_by="user@example.com",
            approve_url="https://example.com/approve",
            expires_at=expires_at,
            items=_make_items(),
            created_by="user_1",
        )
        await AccessRequestRepository.decide_item(
            session,
            request.items[0].id,
            AccessRequestItemStatus.APPROVED,
            decided_by="admin_1",
        )
        result = await AccessRequestRepository.amend_item(
            session,
            request.items[0].id,
            rules=[{"effect": "deny"}],
        )
        assert result is None
        await session.commit()
