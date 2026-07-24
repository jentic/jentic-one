"""Integration tests for AccessRequestService — lifecycle, scoping, and events."""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, select, text

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.repos import EventRepository
from jentic_one.control.core.schema.access_request_items import AccessRequestItem
from jentic_one.control.core.schema.access_requests import AccessRequest
from jentic_one.control.repos.effects_repo import EffectsRepository
from jentic_one.control.repos.toolkit_binding_repo import ToolkitBindingRepository
from jentic_one.control.repos.toolkit_permission_repo import ToolkitPermissionRepository
from jentic_one.control.services.access_requests.errors import (
    AccessRequestNotFoundError,
    AdminEffectReconcileError,
    DuplicatePendingError,
    ItemNotOnRequestError,
    ItemNotPendingError,
    NotAReviewerError,
    PrerequisiteNotMetError,
    RequestNotPendingError,
    UnsupportedScopeGrantError,
)
from jentic_one.control.services.access_requests.service import AccessRequestService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType
from jentic_one.shared.scopes import GRANTABLE_SCOPES

pytestmark = pytest.mark.integration


FILER_SUB = "agnt_filer_001"
OWNER_SUB = "usr_owner_001"
REVIEWER_SUB = "usr_reviewer_001"
UNRELATED_SUB = "usr_unrelated_001"
ADMIN_SUB = "usr_admin_001"


def _filer_identity() -> Identity:
    return Identity(
        sub=FILER_SUB,
        email="filer@test.local",
        permissions=[],
        actor_type=ActorType.AGENT,
        parent_actor_id=OWNER_SUB,
    )


def _owner_identity() -> Identity:
    return Identity(
        sub=OWNER_SUB,
        email="owner@test.local",
        permissions=["agents:write"],
    )


def _reviewer_identity() -> Identity:
    return Identity(
        sub=REVIEWER_SUB,
        email="reviewer@test.local",
        permissions=["agents:write"],
    )


def _unrelated_identity() -> Identity:
    return Identity(
        sub=UNRELATED_SUB,
        email="unrelated@test.local",
        permissions=["agents:write"],
    )


def _admin_identity() -> Identity:
    return Identity(
        sub=ADMIN_SUB,
        email="admin@test.local",
        permissions=["org:admin"],
    )


@pytest.fixture()
async def clean_access_requests(control_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async with control_db.session() as session:
        await session.execute(delete(AccessRequestItem))
        await session.execute(delete(AccessRequest))
        await session.commit()
    yield
    async with control_db.session() as session:
        await session.execute(delete(AccessRequestItem))
        await session.execute(delete(AccessRequest))
        await session.commit()


@pytest.fixture()
async def clean_events(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    async with admin_db.session() as session:
        await session.execute(delete(Event))
        await session.commit()
    yield
    async with admin_db.session() as session:
        await session.execute(delete(Event))
        await session.commit()


@pytest.fixture()
async def seed_binding(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Seed an agent + binding so prerequisite checks pass."""
    async with admin_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO agents (id, name, registered_by, status) "
                "VALUES (:id, :name, :registered_by, 'active') "
                "ON CONFLICT DO NOTHING"
            ),
            {"id": FILER_SUB, "name": "test-filer-agent", "registered_by": OWNER_SUB},
        )
        await session.execute(
            text(
                "INSERT INTO agent_toolkit_bindings (id, agent_id, toolkit_id) "
                "VALUES (:id, :agent_id, :toolkit_id) "
                "ON CONFLICT DO NOTHING"
            ),
            {"id": "atb_test_binding_001", "agent_id": FILER_SUB, "toolkit_id": "tk_target"},
        )
        await session.commit()
    yield
    async with admin_db.session() as session:
        await session.execute(
            text("DELETE FROM agent_toolkit_bindings WHERE id = :id"),
            {"id": "atb_test_binding_001"},
        )
        await session.execute(
            text("DELETE FROM agents WHERE id = :id"),
            {"id": FILER_SUB},
        )
        await session.commit()


@pytest.fixture()
def svc(integration_context: Context) -> AccessRequestService:
    return AccessRequestService(integration_context)


def _base_items() -> list[dict[str, object]]:
    return [
        {
            "resource_type": "credential",
            "action": "read",
            "resource_id": "cred_001",
            "to_type": "toolkit",
            "to_id": "tk_target",
        }
    ]


async def test_file_happy_path(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
) -> None:
    identity = _filer_identity()
    view = await svc.file(
        actor_id=FILER_SUB,
        reason="Need credential access",
        items=_base_items(),
        identity=identity,
    )
    assert view.id.startswith("areq_")
    assert view.status == "pending"
    assert view.created_by == FILER_SUB
    assert view.filer_owner_id == OWNER_SUB
    assert "/access-requests/" in view.approve_url
    assert view.expires_at > dt.datetime.now(dt.UTC)


async def test_file_scope_grant_rejects_unknown_scope(
    svc: AccessRequestService,
    clean_access_requests: None,
) -> None:
    """A scope:grant for a phantom scope is rejected at file time (#672)."""
    identity = _filer_identity()
    items = [{"resource_type": "scope", "action": "grant", "resource_id": "read:catalog"}]
    with pytest.raises(UnsupportedScopeGrantError):
        await svc.file(
            actor_id=FILER_SUB,
            reason="please grant catalog read",
            items=items,
            identity=identity,
        )
    # The guard runs before the transaction, so nothing is persisted.
    page = await svc.list_all(identity=identity)
    assert len(page.data) == 0


async def test_file_scope_grant_accepts_grantable_scope(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
) -> None:
    """A scope:grant for an allow-listed scope files successfully (#672)."""
    identity = _filer_identity()
    scope = next(iter(GRANTABLE_SCOPES))
    items = [{"resource_type": "scope", "action": "grant", "resource_id": scope}]
    view = await svc.file(
        actor_id=FILER_SUB,
        reason="legitimate grant",
        items=items,
        identity=identity,
    )
    assert view.status == "pending"


async def test_file_ttl_honored(
    svc: AccessRequestService,
    clean_access_requests: None,
    seed_binding: None,
    integration_context: Context,
) -> None:
    identity = _filer_identity()
    view = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=_base_items(),
        identity=identity,
    )
    ttl = integration_context.config.control.access_requests.ttl_days
    expected_min = dt.datetime.now(dt.UTC) + dt.timedelta(days=ttl - 1)
    assert view.expires_at > expected_min


async def test_file_prerequisite_not_met(
    svc: AccessRequestService,
    clean_access_requests: None,
) -> None:
    identity = _filer_identity()
    with pytest.raises(PrerequisiteNotMetError):
        await svc.file(
            actor_id=FILER_SUB,
            reason=None,
            items=_base_items(),
            identity=identity,
        )


async def test_file_no_prerequisite_check_without_to_id(
    svc: AccessRequestService,
    clean_access_requests: None,
) -> None:
    identity = _filer_identity()
    items = [{"resource_type": "credential", "action": "read", "resource_id": "cred_001"}]
    view = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=items,
        identity=identity,
    )
    assert view.status == "pending"


async def test_file_duplicate_raises(
    svc: AccessRequestService,
    clean_access_requests: None,
    seed_binding: None,
) -> None:
    identity = _filer_identity()
    first = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=_base_items(),
        identity=identity,
    )
    with pytest.raises(DuplicatePendingError) as exc_info:
        await svc.file(
            actor_id=FILER_SUB,
            reason=None,
            items=_base_items(),
            identity=identity,
        )
    assert exc_info.value.existing_request_id == first.id
    assert exc_info.value.approve_url == first.approve_url


async def test_decide_approve_and_deny(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
) -> None:
    filer = _filer_identity()
    items = [
        {
            "resource_type": "credential",
            "action": "read",
            "resource_id": "cred_001",
            "to_type": "toolkit",
            "to_id": "tk_target",
        },
        {
            "resource_type": "credential",
            "action": "write",
            "resource_id": "cred_002",
            "to_type": "toolkit",
            "to_id": "tk_target",
        },
    ]
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=items,
        identity=filer,
    )

    reviewer = _owner_identity()
    view = await svc.decide(
        filed.id,
        identity=reviewer,
        item_decisions=[
            {
                "item_id": filed.items[0].id,
                "decision": "approved",
            },
            {
                "item_id": filed.items[1].id,
                "decision": "denied",
                "decision_reason": "Not needed",
            },
        ],
    )
    assert view.status == "partially_approved"
    approved_item = next(i for i in view.items if i.id == filed.items[0].id)
    denied_item = next(i for i in view.items if i.id == filed.items[1].id)
    assert approved_item.status == "approved"
    assert approved_item.applied_effects is not None
    assert approved_item.applied_effects["skipped"] is True
    assert denied_item.status == "denied"
    assert denied_item.decision_reason == "Not needed"


async def test_decide_all_approved(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
) -> None:
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=_base_items(),
        identity=filer,
    )

    reviewer = _owner_identity()
    view = await svc.decide(
        filed.id,
        identity=reviewer,
        item_decisions=[{"item_id": filed.items[0].id, "decision": "approved"}],
    )
    assert view.status == "approved"


async def test_decide_all_denied(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
) -> None:
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=_base_items(),
        identity=filer,
    )

    reviewer = _owner_identity()
    view = await svc.decide(
        filed.id,
        identity=reviewer,
        item_decisions=[{"item_id": filed.items[0].id, "decision": "denied"}],
    )
    assert view.status == "denied"


async def test_decide_approve_unresolvable_toolkit_ref_denies_not_pending(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
) -> None:
    """Regression (#696): approving a ``toolkit:bind`` whose reference resolves to
    no toolkit must *deny the item with the failure as the reason* — not raise and
    roll the decision back, leaving the request stranded as ``pending``.

    Pre-fix, ``decide()`` flipped the item to approved, then ``validate()`` raised
    ``ToolkitReferenceUnresolvedError`` → the whole control-DB transaction rolled
    back → the item snapped back to ``pending`` and the agent's ``--wait`` timed
    out blind. Now the loop closes: the request leaves pending as ``denied`` and
    carries an actionable ``decision_reason``.
    """
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason="bind me to sheets",
        items=[
            {
                "resource_type": "toolkit",
                "action": "bind",
                "resource_reference": {"vendor": "no-such-vendor", "name": "no-such-api"},
            }
        ],
        identity=filer,
    )
    assert filed.status == "pending"

    reviewer = _owner_identity()
    view = await svc.decide(
        filed.id,
        identity=reviewer,
        item_decisions=[{"item_id": filed.items[0].id, "decision": "approved"}],
    )

    assert view.status == "denied"
    item = view.items[0]
    assert item.status == "denied"
    assert item.decision_reason is not None
    assert "No toolkit serves API" in item.decision_reason
    assert "no-such-vendor/no-such-api" in item.decision_reason

    # The denial is durable, not just in the returned view: a re-read sees the
    # terminal state (so a polling agent observes the closed loop).
    reread = await svc.get(filed.id, identity=reviewer)
    assert reread.status == "denied"
    assert reread.items[0].decision_reason == item.decision_reason


async def test_decide_not_reviewer_raises(
    svc: AccessRequestService,
    clean_access_requests: None,
    seed_binding: None,
) -> None:
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=_base_items(),
        identity=filer,
    )
    with pytest.raises(NotAReviewerError):
        await svc.decide(
            filed.id,
            identity=filer,
            item_decisions=[{"item_id": filed.items[0].id, "decision": "approved"}],
        )


async def test_decide_not_pending_raises(
    svc: AccessRequestService,
    clean_access_requests: None,
    seed_binding: None,
) -> None:
    """Re-deciding an item with a *different* verdict is a conflict (issue #625).

    decide() no longer hard-fails on a non-pending request (that path is now
    reconcilable on retry); a genuine conflict — requesting DENY for an item that
    is already APPROVED — surfaces as an item-level ItemNotPendingError instead.
    """
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=_base_items(),
        identity=filer,
    )
    reviewer = _owner_identity()
    await svc.decide(
        filed.id,
        identity=reviewer,
        item_decisions=[{"item_id": filed.items[0].id, "decision": "approved"}],
    )
    with pytest.raises(ItemNotPendingError):
        await svc.decide(
            filed.id,
            identity=reviewer,
            item_decisions=[{"item_id": filed.items[0].id, "decision": "denied"}],
        )


async def test_decide_item_not_on_request(
    svc: AccessRequestService,
    clean_access_requests: None,
    seed_binding: None,
) -> None:
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=_base_items(),
        identity=filer,
    )
    reviewer = _owner_identity()
    with pytest.raises(ItemNotOnRequestError):
        await svc.decide(
            filed.id,
            identity=reviewer,
            item_decisions=[{"item_id": "arqi_nonexistent", "decision": "approved"}],
        )


async def test_amend_updates_rules(
    svc: AccessRequestService,
    clean_access_requests: None,
    seed_binding: None,
) -> None:
    filer = _filer_identity()
    # Rules only enforce on a credential:bind, so amending them is only valid for
    # that item type (a credential:read carries no enforceable rules).
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=[
            {
                "resource_type": "credential",
                "action": "bind",
                "resource_id": "cred_001",
                "to_type": "toolkit",
                "to_id": "tk_target",
            }
        ],
        identity=filer,
    )
    new_rules = [{"effect": "allow", "methods": ["GET", "POST"]}]
    view = await svc.amend(
        filed.id,
        identity=filer,
        item_amendments=[{"item_id": filed.items[0].id, "rules": new_rules}],
    )
    assert view.items[0].rules == new_rules


async def test_amend_not_pending_raises(
    svc: AccessRequestService,
    clean_access_requests: None,
    seed_binding: None,
) -> None:
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=_base_items(),
        identity=filer,
    )
    reviewer = _owner_identity()
    await svc.decide(
        filed.id,
        identity=reviewer,
        item_decisions=[{"item_id": filed.items[0].id, "decision": "approved"}],
    )
    with pytest.raises(RequestNotPendingError):
        await svc.amend(
            filed.id,
            identity=filer,
            item_amendments=[{"item_id": filed.items[0].id, "rules": [{"effect": "deny"}]}],
        )


async def test_amend_item_not_on_request(
    svc: AccessRequestService,
    clean_access_requests: None,
    seed_binding: None,
) -> None:
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=_base_items(),
        identity=filer,
    )
    with pytest.raises(ItemNotOnRequestError):
        await svc.amend(
            filed.id,
            identity=filer,
            item_amendments=[{"item_id": "arqi_nonexistent", "rules": [{"effect": "deny"}]}],
        )


async def test_amend_scope_grant_rejects_ungrantable_resource_id(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
) -> None:
    """Amending a scope:grant's resource_id to a non-allow-listed scope is rejected (#672).

    The file-time guard must also run on amend so an agent can't first file a
    grantable scope and then rewrite it to a privileged one, leaving a
    misleading 'pending' item that could never apply.
    """
    filer = _filer_identity()
    scope = next(iter(GRANTABLE_SCOPES))
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason="legitimate grant",
        items=[{"resource_type": "scope", "action": "grant", "resource_id": scope}],
        identity=filer,
    )
    with pytest.raises(UnsupportedScopeGrantError):
        await svc.amend(
            filed.id,
            identity=filer,
            item_amendments=[{"item_id": filed.items[0].id, "resource_id": "org:admin"}],
        )
    # The guard runs before amend_item writes, so the item is unchanged.
    refreshed = await svc.get(filed.id, identity=filer)
    assert refreshed.items[0].resource_id == scope


async def test_withdraw_sets_withdrawn(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
) -> None:
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=_base_items(),
        identity=filer,
    )
    view = await svc.withdraw(filed.id, identity=filer)
    assert view.status == "withdrawn"
    assert all(i.status == "withdrawn" for i in view.items)


async def test_withdraw_not_pending_raises(
    svc: AccessRequestService,
    clean_access_requests: None,
    seed_binding: None,
) -> None:
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=_base_items(),
        identity=filer,
    )
    await svc.withdraw(filed.id, identity=filer)
    with pytest.raises(RequestNotPendingError):
        await svc.withdraw(filed.id, identity=filer)


async def test_expiry_computed_in_get(
    svc: AccessRequestService,
    clean_access_requests: None,
    seed_binding: None,
    control_db: DatabaseSession,
) -> None:
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=_base_items(),
        identity=filer,
    )
    past = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    async with control_db.session() as session:
        await session.execute(
            text("UPDATE access_requests SET expires_at = :exp WHERE id = :id"),
            {"exp": past, "id": filed.id},
        )
        await session.commit()

    view = await svc.get(filed.id, identity=filer)
    assert view.status == "expired"


async def test_visibility_filer_sees_own(
    svc: AccessRequestService,
    clean_access_requests: None,
    seed_binding: None,
) -> None:
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=_base_items(),
        identity=filer,
    )
    view = await svc.get(filed.id, identity=filer)
    assert view.id == filed.id


async def test_visibility_owner_sees_filer_request(
    svc: AccessRequestService,
    clean_access_requests: None,
    seed_binding: None,
) -> None:
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=_base_items(),
        identity=filer,
    )
    owner = _owner_identity()
    view = await svc.get(filed.id, identity=owner)
    assert view.id == filed.id


async def test_visibility_unrelated_user_not_found(
    svc: AccessRequestService,
    clean_access_requests: None,
    seed_binding: None,
) -> None:
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=_base_items(),
        identity=filer,
    )
    unrelated = _unrelated_identity()
    with pytest.raises(AccessRequestNotFoundError):
        await svc.get(filed.id, identity=unrelated)


async def test_visibility_unrelated_user_list_excludes(
    svc: AccessRequestService,
    clean_access_requests: None,
    seed_binding: None,
) -> None:
    filer = _filer_identity()
    await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=_base_items(),
        identity=filer,
    )
    unrelated = _unrelated_identity()
    page = await svc.list_all(identity=unrelated)
    assert len(page.data) == 0


async def test_visibility_admin_sees_all(
    svc: AccessRequestService,
    clean_access_requests: None,
    seed_binding: None,
) -> None:
    filer = _filer_identity()
    await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=_base_items(),
        identity=filer,
    )
    admin = _admin_identity()
    page = await svc.list_all(identity=admin)
    assert len(page.data) == 1


async def test_visibility_unrelated_withdraw_raises(
    svc: AccessRequestService,
    clean_access_requests: None,
    seed_binding: None,
) -> None:
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=_base_items(),
        identity=filer,
    )
    unrelated = _unrelated_identity()
    with pytest.raises(AccessRequestNotFoundError):
        await svc.withdraw(filed.id, identity=unrelated)


async def test_event_filed(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
    admin_db: DatabaseSession,
) -> None:
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=_base_items(),
        identity=filer,
    )
    async with admin_db.session() as session:
        events = await EventRepository.list_all(session, event_type=["access_request.filed"])
    assert len(events) == 1
    assert events[0].type == "access_request.filed"
    assert events[0].requires_action is True
    assert events[0].data["request_id"] == filed.id
    assert events[0].data["status"] == "pending"


async def test_event_approved(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
    admin_db: DatabaseSession,
) -> None:
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=_base_items(),
        identity=filer,
    )
    reviewer = _owner_identity()
    await svc.decide(
        filed.id,
        identity=reviewer,
        item_decisions=[{"item_id": filed.items[0].id, "decision": "approved"}],
    )
    async with admin_db.session() as session:
        events = await EventRepository.list_all(session, event_type=["access_request.approved"])
    assert len(events) == 1
    assert events[0].data["status"] == "approved"


async def test_event_denied(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
    admin_db: DatabaseSession,
) -> None:
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=_base_items(),
        identity=filer,
    )
    reviewer = _owner_identity()
    await svc.decide(
        filed.id,
        identity=reviewer,
        item_decisions=[{"item_id": filed.items[0].id, "decision": "denied"}],
    )
    async with admin_db.session() as session:
        events = await EventRepository.list_all(session, event_type=["access_request.denied"])
    assert len(events) == 1
    assert events[0].data["status"] == "denied"


async def test_event_withdrawn(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
    admin_db: DatabaseSession,
) -> None:
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=_base_items(),
        identity=filer,
    )
    await svc.withdraw(filed.id, identity=filer)
    async with admin_db.session() as session:
        events = await EventRepository.list_all(session, event_type=["access_request.withdrawn"])
    assert len(events) == 1
    assert events[0].data["request_id"] == filed.id
    assert events[0].data["status"] == "withdrawn"


async def test_event_not_emitted_when_still_pending(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
    admin_db: DatabaseSession,
) -> None:
    """A decide that leaves the request still 'pending' emits no decide event."""
    filer = _filer_identity()
    items = [
        {
            "resource_type": "credential",
            "action": "read",
            "resource_id": "cred_001",
            "to_type": "toolkit",
            "to_id": "tk_target",
        },
        {
            "resource_type": "credential",
            "action": "write",
            "resource_id": "cred_002",
            "to_type": "toolkit",
            "to_id": "tk_target",
        },
    ]
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=items,
        identity=filer,
    )
    reviewer = _owner_identity()
    view = await svc.decide(
        filed.id,
        identity=reviewer,
        item_decisions=[{"item_id": filed.items[0].id, "decision": "approved"}],
    )
    assert view.status == "pending"

    async with admin_db.session() as session:
        events = await EventRepository.list_all(
            session,
            event_type=[
                "access_request.approved",
                "access_request.denied",
            ],
        )
    assert len(events) == 0


async def test_approve_credential_bind_creates_binding_and_rules(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
    control_db: DatabaseSession,
) -> None:
    """Approving a credential-bind request creates the credential binding and permission rules."""
    filer = _filer_identity()

    async with control_db.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO toolkits (id, name, created_by) "
                "VALUES (:id, :name, :created_by) "
                "ON CONFLICT DO NOTHING"
            ),
            {"id": "tk_target", "name": "test-toolkit-for-bind", "created_by": OWNER_SUB},
        )
        await session.execute(
            text(
                "INSERT INTO credentials (id, type, name, api_vendor, created_by) "
                "VALUES (:id, :type, :name, :vendor, :created_by) "
                "ON CONFLICT DO NOTHING"
            ),
            {
                "id": "cred_bind_001",
                "type": "token",
                "name": "test-cred",
                "vendor": "test-vendor",
                "created_by": OWNER_SUB,
            },
        )

    items = [
        {
            "resource_type": "credential",
            "action": "bind",
            "resource_id": "cred_bind_001",
            "to_type": "toolkit",
            "to_id": "tk_target",
            "rules": [
                {"effect": "allow", "methods": ["GET"], "path": "^/pets"},
            ],
        }
    ]
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason="Need credential binding",
        items=items,
        identity=filer,
    )

    reviewer = _owner_identity()
    view = await svc.decide(
        filed.id,
        identity=reviewer,
        item_decisions=[{"item_id": filed.items[0].id, "decision": "approved"}],
    )

    assert view.status == "approved"
    approved_item = view.items[0]
    assert approved_item.applied_effects is not None
    assert "binding_id" in approved_item.applied_effects
    assert approved_item.applied_effects["rules_applied"] == 1
    assert approved_item.applied_effects["already_bound"] is False

    async with control_db.session() as session:
        binding = await ToolkitBindingRepository.get(session, "tk_target", "cred_bind_001")
        assert binding is not None

        rules = await ToolkitPermissionRepository.list_rules(session, "tk_target", "cred_bind_001")
        assert len(rules) >= 1
        assert any(r.effect == "allow" and r.path == "^/pets" for r in rules)

    async with control_db.transaction() as session:
        await session.execute(
            text(
                "DELETE FROM toolkit_permission_rules "
                "WHERE toolkit_id = :tk AND credential_id = :cred"
            ),
            {"tk": "tk_target", "cred": "cred_bind_001"},
        )
        await session.execute(
            text(
                "DELETE FROM toolkit_credential_bindings "
                "WHERE toolkit_id = :tk AND credential_id = :cred"
            ),
            {"tk": "tk_target", "cred": "cred_bind_001"},
        )
        await session.execute(
            text("DELETE FROM credentials WHERE id = :id"),
            {"id": "cred_bind_001"},
        )
        await session.execute(
            text("DELETE FROM toolkits WHERE id = :id"),
            {"id": "tk_target"},
        )


# --- cross-DB reconcile (issue #625) ---

SCOPE_GRANT = "owner:toolkits:read"
TOOLKIT_BIND_TARGET = "tk_admin_bind_001"


def _admin_effect_items() -> list[dict[str, object]]:
    """A toolkit-bind and a scope-grant — both applied as admin-DB effects."""
    return [
        {
            "resource_type": "toolkit",
            "action": "bind",
            "resource_id": TOOLKIT_BIND_TARGET,
        },
        {
            "resource_type": "scope",
            "action": "grant",
            "resource_id": SCOPE_GRANT,
        },
    ]


@pytest.fixture()
async def clean_admin_effects(
    admin_db: DatabaseSession, control_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    """Remove any toolkit-bind / scope-grant rows produced by reconcile tests.

    Also seeds the bind-target toolkit (owned by ``OWNER_SUB``) so the decider's
    owner-scoped visibility check on ``toolkit:bind`` passes.
    """

    async def _cleanup() -> None:
        async with admin_db.session() as session:
            await session.execute(
                text("DELETE FROM agent_toolkit_bindings WHERE toolkit_id = :tk"),
                {"tk": TOOLKIT_BIND_TARGET},
            )
            await session.execute(
                text("DELETE FROM actor_scope_grants WHERE actor_id = :aid AND scope = :scope"),
                {"aid": FILER_SUB, "scope": SCOPE_GRANT},
            )
            await session.commit()
        async with control_db.session() as session:
            await session.execute(
                text("DELETE FROM toolkits WHERE id = :id"),
                {"id": TOOLKIT_BIND_TARGET},
            )
            await session.commit()

    await _cleanup()
    async with control_db.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO toolkits (id, name, created_by) "
                "VALUES (:id, :name, :created_by) "
                "ON CONFLICT DO NOTHING"
            ),
            {"id": TOOLKIT_BIND_TARGET, "name": "test-toolkit-admin-bind", "created_by": OWNER_SUB},
        )
    yield
    await _cleanup()


async def _decided_items(
    control_db: DatabaseSession, request_id: str
) -> dict[str, AccessRequestItem]:
    async with control_db.session() as session:
        result = await session.execute(
            select(AccessRequestItem).where(AccessRequestItem.access_request_id == request_id)
        )
        return {item.resource_type: item for item in result.scalars().all()}


async def _count_scope_grants(admin_db: DatabaseSession) -> int:
    async with admin_db.session() as session:
        result = await session.execute(
            text(
                "SELECT count(*) FROM actor_scope_grants WHERE actor_id = :aid AND scope = :scope"
            ),
            {"aid": FILER_SUB, "scope": SCOPE_GRANT},
        )
        return int(result.scalar_one())


async def _count_toolkit_bindings(admin_db: DatabaseSession) -> int:
    async with admin_db.session() as session:
        result = await session.execute(
            text(
                "SELECT count(*) FROM agent_toolkit_bindings "
                "WHERE agent_id = :aid AND toolkit_id = :tk"
            ),
            {"aid": FILER_SUB, "tk": TOOLKIT_BIND_TARGET},
        )
        return int(result.scalar_one())


async def test_decide_mid_apply_failure_leaves_no_orphans(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
    clean_admin_effects: None,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed admin effect leaves the decision durable and the request reconcilable."""
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason="Need admin effects",
        items=_admin_effect_items(),
        identity=filer,
    )

    original_grant = EffectsRepository.grant_scope_to_actor

    async def _boom(*args: object, **kwargs: object) -> bool:
        raise RuntimeError("simulated admin-DB failure")

    monkeypatch.setattr(EffectsRepository, "grant_scope_to_actor", staticmethod(_boom))

    reviewer = _owner_identity()
    decisions = [{"item_id": item.id, "decision": "approved"} for item in filed.items]

    with pytest.raises(AdminEffectReconcileError):
        await svc.decide(filed.id, identity=reviewer, item_decisions=decisions)

    # Decision is durable: both items APPROVED (phase 1 committed).
    items = await _decided_items(control_db, filed.id)
    assert items["toolkit"].status == "approved"
    assert items["scope"].status == "approved"
    # The toolkit-bind succeeded and is acked; the scope-grant is un-acked.
    assert items["toolkit"].applied_effects is not None
    assert items["scope"].applied_effects is None

    # Admin DB has exactly the toolkit binding, no scope grant.
    assert await _count_toolkit_bindings(admin_db) == 1
    assert await _count_scope_grants(admin_db) == 0

    monkeypatch.setattr(EffectsRepository, "grant_scope_to_actor", original_grant)


async def test_decide_retry_reconciles(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
    clean_admin_effects: None,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a partial failure, calling decide() again drives the un-acked effect."""
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason="Need admin effects",
        items=_admin_effect_items(),
        identity=filer,
    )
    reviewer = _owner_identity()
    decisions = [{"item_id": item.id, "decision": "approved"} for item in filed.items]

    original_grant = EffectsRepository.grant_scope_to_actor

    async def _boom(*args: object, **kwargs: object) -> bool:
        raise RuntimeError("simulated admin-DB failure")

    monkeypatch.setattr(EffectsRepository, "grant_scope_to_actor", staticmethod(_boom))
    with pytest.raises(AdminEffectReconcileError):
        await svc.decide(filed.id, identity=reviewer, item_decisions=decisions)

    # Retry with the same decisions — now succeeds.
    monkeypatch.setattr(EffectsRepository, "grant_scope_to_actor", original_grant)
    view = await svc.decide(filed.id, identity=reviewer, item_decisions=decisions)

    assert view.status == "approved"
    items = await _decided_items(control_db, filed.id)
    assert items["toolkit"].applied_effects is not None
    assert items["scope"].applied_effects is not None

    # Each admin row exists exactly once (ON CONFLICT idempotency).
    assert await _count_toolkit_bindings(admin_db) == 1
    assert await _count_scope_grants(admin_db) == 1


async def test_decide_idempotent_recall(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
    clean_admin_effects: None,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
) -> None:
    """A verbatim re-call after success is a no-op: no error, no dup rows, no dup event."""
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason="Need admin effects",
        items=_admin_effect_items(),
        identity=filer,
    )
    reviewer = _owner_identity()
    decisions = [{"item_id": item.id, "decision": "approved"} for item in filed.items]

    first = await svc.decide(filed.id, identity=reviewer, item_decisions=decisions)
    assert first.status == "approved"

    second = await svc.decide(filed.id, identity=reviewer, item_decisions=decisions)
    assert second.status == "approved"
    assert {i.id: i.applied_effects for i in second.items} == {
        i.id: i.applied_effects for i in first.items
    }

    # No duplicate admin rows.
    assert await _count_toolkit_bindings(admin_db) == 1
    assert await _count_scope_grants(admin_db) == 1

    # No duplicate decision event — exactly one approved event from the first call.
    async with admin_db.session() as session:
        events = await EventRepository.list_all(session, event_type=["access_request.approved"])
    assert len([e for e in events if e.data["request_id"] == filed.id]) == 1


async def test_decide_conflict_raises_item_not_pending(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
    clean_admin_effects: None,
) -> None:
    """Requesting a different decision for an already-decided item is a conflict."""
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=_admin_effect_items(),
        identity=filer,
    )
    reviewer = _owner_identity()
    decisions = [{"item_id": item.id, "decision": "approved"} for item in filed.items]
    await svc.decide(filed.id, identity=reviewer, item_decisions=decisions)

    with pytest.raises(ItemNotPendingError):
        await svc.decide(
            filed.id,
            identity=reviewer,
            item_decisions=[{"item_id": filed.items[0].id, "decision": "denied"}],
        )


# --- file-time fulfillability advisory (theme 3 residual) ---


async def _list_events_by_type(admin_db: DatabaseSession, event_type: str) -> list[Event]:
    async with admin_db.session() as session:
        return await EventRepository.list_all(session, event_type=[event_type])


async def test_file_emits_unserved_advisory_for_plain_toolkit_bind_with_no_serving_toolkit(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
    admin_db: DatabaseSession,
) -> None:
    """A plain `toolkit:bind` by reference with no serving toolkit emits an advisory."""
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason="bind me to a not-yet-served api",
        items=[
            {
                "resource_type": "toolkit",
                "action": "bind",
                "resource_reference": {"vendor": "no-such-vendor", "name": "no-such-api"},
            }
        ],
        identity=filer,
    )
    assert filed.status == "pending"  # advisory doesn't block the filing

    events = await _list_events_by_type(admin_db, "broker.toolkit_binding_unserved")
    matching = [e for e in events if e.data.get("request_id") == filed.id]
    assert len(matching) == 1
    event = matching[0]
    assert event.severity == "warning"
    assert event.data["api"] == {
        "vendor": "no-such-vendor",
        "name": "no-such-api",
        "version": None,
    }
    assert "no-such-vendor/no-such-api" in event.summary


async def test_file_skips_unserved_advisory_when_toolkit_serves_api(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
) -> None:
    """When a toolkit already serves the referenced API, no advisory fires."""
    async with control_db.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO toolkits (id, name, created_by) "
                "VALUES (:id, :name, :created_by) "
                "ON CONFLICT DO NOTHING"
            ),
            {
                "id": "tk_served",
                "name": "served-toolkit",
                "created_by": OWNER_SUB,
            },
        )
        await session.execute(
            text(
                "INSERT INTO credentials "
                "(id, type, name, api_vendor, api_name, created_by) "
                "VALUES (:id, 'token', :name, :vendor, :api_name, :created_by) "
                "ON CONFLICT DO NOTHING"
            ),
            {
                "id": "cred_served_001",
                "name": "served-cred",
                "vendor": "servedvendor",
                "api_name": "widgets",
                "created_by": OWNER_SUB,
            },
        )
        await EffectsRepository.bind_credential_to_toolkit(
            session,
            toolkit_id="tk_served",
            credential_id="cred_served_001",
            created_by=OWNER_SUB,
        )

    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason="bind me to a served api",
        items=[
            {
                "resource_type": "toolkit",
                "action": "bind",
                "resource_reference": {"vendor": "servedvendor", "name": "widgets"},
            }
        ],
        identity=filer,
    )
    assert filed.status == "pending"

    events = await _list_events_by_type(admin_db, "broker.toolkit_binding_unserved")
    matching = [e for e in events if e.data.get("request_id") == filed.id]
    assert matching == []


async def test_file_skips_unserved_advisory_when_request_carries_fulfilment_intent(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
    admin_db: DatabaseSession,
) -> None:
    """Plans expect nothing to serve the API yet — the advisory must stay silent."""
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason="provision then bind",
        items=[
            {
                "resource_type": "toolkit",
                "action": "create",
                "resource_reference": {"vendor": "brandnew", "name": "widgets"},
            },
            {
                "resource_type": "credential",
                "action": "provision",
                "resource_reference": {"vendor": "brandnew", "name": "widgets"},
            },
            {
                "resource_type": "toolkit",
                "action": "bind",
                "resource_reference": {"vendor": "brandnew", "name": "widgets"},
            },
        ],
        identity=filer,
    )
    assert filed.status == "pending"

    events = await _list_events_by_type(admin_db, "broker.toolkit_binding_unserved")
    matching = [e for e in events if e.data.get("request_id") == filed.id]
    assert matching == []


async def test_file_skips_unserved_advisory_when_bind_names_toolkit_by_id(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
    admin_db: DatabaseSession,
) -> None:
    """A `toolkit:bind` with an explicit id (not a reference) is not by-name — no advisory."""
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason="bind by id",
        items=[
            {
                "resource_type": "toolkit",
                "action": "bind",
                "resource_id": "tk_target",
            }
        ],
        identity=filer,
    )
    assert filed.status == "pending"

    events = await _list_events_by_type(admin_db, "broker.toolkit_binding_unserved")
    matching = [e for e in events if e.data.get("request_id") == filed.id]
    assert matching == []
