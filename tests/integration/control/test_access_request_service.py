"""Integration tests for AccessRequestService — lifecycle, scoping, and events.

Theme-5 Phase 3: ``credential:bind`` binds the filing agent directly to a
credential (two-stage admin effect — control-DB rules first, then the admin
binding row); the toolkit vocabulary is retired; reference resolution runs
under the decider's owner axis (hard problem 8).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, select, text

from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.repos import EventRepository
from jentic_one.control.core.schema.access_request_items import AccessRequestItem
from jentic_one.control.core.schema.access_requests import AccessRequest
from jentic_one.control.repos.agent_permission_rule_repo import AgentPermissionRuleRepository
from jentic_one.control.repos.effects_repo import EffectsRepository
from jentic_one.control.services.access_requests.errors import (
    AccessRequestNotFoundError,
    AdminEffectReconcileError,
    DuplicatePendingError,
    ItemNotOnRequestError,
    ItemNotPendingError,
    NotAReviewerError,
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

_RULES = [{"effect": "allow", "methods": ["GET"], "path": "^/pets"}]


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
async def seed_binding(
    admin_db: DatabaseSession, control_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    """Seed the filer agent (admin DB) + the owner's credentials (control DB).

    ``credential:bind`` writes an admin ``agent_credential_bindings`` row keyed
    by the filing agent, so the agent must exist; the bind's target credentials
    (cred_001/cred_002, owned by OWNER_SUB) must be visible to the deciding
    owner. Teardown removes the binding rows and rules the approvals created.
    """

    async def _cleanup() -> None:
        async with admin_db.session() as session:
            await session.execute(
                text("DELETE FROM agent_credential_bindings WHERE agent_id = :aid"),
                {"aid": FILER_SUB},
            )
            await session.execute(
                text("DELETE FROM agents WHERE id = :aid"),
                {"aid": FILER_SUB},
            )
            await session.commit()
        async with control_db.session() as session:
            await session.execute(
                text("DELETE FROM agent_permission_rules WHERE agent_id = :aid"),
                {"aid": FILER_SUB},
            )
            await session.execute(
                text("DELETE FROM credentials WHERE id IN ('cred_001', 'cred_002')")
            )
            await session.commit()

    await _cleanup()
    async with admin_db.session() as session:
        # agents.owner_id is FK'd to users — the owner must exist on the roster.
        await session.execute(
            text(
                "INSERT INTO users (id, email, first_name, last_name) "
                "VALUES (:id, :email, 'Olive', 'Owner') ON CONFLICT DO NOTHING"
            ),
            {"id": OWNER_SUB, "email": "owner@test.local"},
        )
        await session.execute(
            text(
                "INSERT INTO agents (id, name, owner_id, registered_by, status) "
                "VALUES (:id, :name, :owner, :registered_by, 'active') "
                "ON CONFLICT DO NOTHING"
            ),
            {
                "id": FILER_SUB,
                "name": "test-filer-agent",
                "owner": OWNER_SUB,
                "registered_by": OWNER_SUB,
            },
        )
        await session.commit()
    async with control_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO credentials (id, type, name, api_vendor, created_by) VALUES "
                "('cred_001', 'token_value', 'itest-cred-1', 'itest-vendor-one', :owner), "
                "('cred_002', 'token_value', 'itest-cred-2', 'itest-vendor-two', :owner) "
                "ON CONFLICT DO NOTHING"
            ),
            {"owner": OWNER_SUB},
        )
        await session.commit()
    yield
    await _cleanup()


@pytest.fixture()
def svc(integration_context: Context) -> AccessRequestService:
    return AccessRequestService(integration_context)


def _base_items() -> list[dict[str, object]]:
    return [
        {
            "resource_type": "credential",
            "action": "bind",
            "resource_id": "cred_001",
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
    # Filing with no policy stamps the read-only default (hard problem 6).
    assert view.items[0].rules == [{"effect": "allow", "methods": ["GET"]}]
    assert view.items[0].rule_set_id is None


async def test_file_rule_set_id_suppresses_default_rules(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
) -> None:
    """A shared-set pointer is a policy: the default-rule substitution must not
    override it (the stored item would then carry both carriers)."""
    view = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=[
            {
                "resource_type": "credential",
                "action": "bind",
                "resource_id": "cred_001",
                "rule_set_id": "prs_pointer_1",
            }
        ],
        identity=_filer_identity(),
    )
    assert view.items[0].rule_set_id == "prs_pointer_1"
    assert view.items[0].rules is None


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
        {"resource_type": "credential", "action": "bind", "resource_id": "cred_001"},
        {"resource_type": "credential", "action": "bind", "resource_id": "cred_002"},
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
    # A credential:bind approval records a real binding effect, not a skip.
    assert approved_item.applied_effects["credential_id"] == "cred_001"
    assert approved_item.applied_effects["binding_id"].startswith("acb_")
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


async def _filed_alert(admin_db: DatabaseSession, request_id: str) -> Event | None:
    async with admin_db.session() as session:
        rows = await EventRepository.list_all(
            session, event_type=["access_request.filed"], limit=100
        )
    return next((e for e in rows if (e.data or {}).get("request_id") == request_id), None)


async def test_decide_settles_filed_alert(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
    admin_db: DatabaseSession,
) -> None:
    """Deciding a request acknowledges its actionable `access_request.filed` alert.

    The filed event is what puts the "review this request" row on the rail and
    dashboard. The decision IS that review — leaving the alert live keeps a
    stale actionable row whose buttons then fail on "already decided". Only the
    decided request's alert may be touched (scoped by data.request_id).
    """
    filer = _filer_identity()
    filed = await svc.file(actor_id=FILER_SUB, reason=None, items=_base_items(), identity=filer)
    other = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=[
            {
                "resource_type": "credential",
                "action": "bind",
                "resource_id": "cred_002",
            }
        ],
        identity=filer,
    )

    reviewer = _owner_identity()
    await svc.decide(
        filed.id,
        identity=reviewer,
        item_decisions=[{"item_id": filed.items[0].id, "decision": "approved"}],
    )

    settled = await _filed_alert(admin_db, filed.id)
    assert settled is not None
    assert settled.acknowledged is True
    assert settled.acknowledged_by == OWNER_SUB

    untouched = await _filed_alert(admin_db, other.id)
    assert untouched is not None
    assert untouched.acknowledged is False


async def test_withdraw_settles_filed_alert(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
    admin_db: DatabaseSession,
) -> None:
    """Withdrawing a request also settles its filed alert — nothing left to review."""
    filer = _filer_identity()
    filed = await svc.file(actor_id=FILER_SUB, reason=None, items=_base_items(), identity=filer)

    await svc.withdraw(filed.id, identity=filer)

    settled = await _filed_alert(admin_db, filed.id)
    assert settled is not None
    assert settled.acknowledged is True


async def test_decide_retry_after_post_commit_crash_still_announces(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
    admin_db: DatabaseSession,
) -> None:
    """A decide() retry recovers a decision that committed but was never announced.

    decide() is documented as safe to retry: phase 1 (the decision) commits to
    the control DB, then post-commit work (admin-effect reconcile, decision
    event, filed-alert settlement) runs. If the process dies between the two,
    the decision is durable but unannounced. Pre-fix, the retry gated the
    announcement purely on ``any_transition`` — which a retry never has — so the
    decision event was never emitted and the actionable ``access_request.filed``
    alert stayed live forever (a stale "review this request" row whose buttons
    then fail on "already decided"). The still-unsettled alert is the durable
    marker: a retry that settles it must also emit the decision event.
    """
    filer = _filer_identity()
    filed = await svc.file(actor_id=FILER_SUB, reason=None, items=_base_items(), identity=filer)
    reviewer = _owner_identity()
    decisions = [{"item_id": filed.items[0].id, "decision": "approved"}]

    # First attempt: the control-DB commit lands, then the post-commit
    # reconcile "crashes" — the decision is durable but never announced.
    original = svc._reconcile_admin_effects

    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated crash after phase-1 commit")

    svc._reconcile_admin_effects = boom  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeError, match="simulated crash"):
            await svc.decide(filed.id, identity=reviewer, item_decisions=decisions)
    finally:
        svc._reconcile_admin_effects = original  # type: ignore[method-assign]

    live = await _filed_alert(admin_db, filed.id)
    assert live is not None
    assert live.acknowledged is False

    # Retry with the same decisions: nothing transitions, but the announcement
    # must be recovered — alert settled AND decision event emitted — and the
    # un-acked admin effect (the crash also skipped it) driven to completion.
    view = await svc.decide(filed.id, identity=reviewer, item_decisions=decisions)
    assert view.status == "approved"
    assert view.items[0].applied_effects is not None

    settled = await _filed_alert(admin_db, filed.id)
    assert settled is not None
    assert settled.acknowledged is True

    async with admin_db.session() as session:
        rows = await EventRepository.list_all(
            session, event_type=["access_request.approved"], limit=100
        )
    decision_events = [e for e in rows if (e.data or {}).get("request_id") == filed.id]
    assert len(decision_events) == 1


async def test_decide_approve_unresolvable_credential_ref_denies_not_pending(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
) -> None:
    """Regression (#696): approving a ``credential:bind`` whose reference resolves
    to no credential must *deny the item with the failure as the reason* — not
    raise and roll the decision back, leaving the request stranded as ``pending``.

    Pre-fix, ``decide()`` flipped the item to approved, then ``validate()`` raised
    the unresolved-reference error → the whole control-DB transaction rolled
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
                "resource_type": "credential",
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
    assert "No credential covers API" in item.decision_reason
    assert "no-such-vendor/no-such-api" in item.decision_reason

    # The denial is durable, not just in the returned view: a re-read sees the
    # terminal state (so a polling agent observes the closed loop).
    reread = await svc.get(filed.id, identity=reviewer)
    assert reread.status == "denied"
    assert reread.items[0].decision_reason == item.decision_reason


# --- decider owner axis (hard problem 8) ---


@pytest.fixture()
async def seed_foreign_credential(
    control_db: DatabaseSession, admin_db: DatabaseSession
) -> AsyncGenerator[None, None]:
    """A credential owned by UNRELATED_SUB covering a distinct vendor."""

    async def _cleanup() -> None:
        async with admin_db.session() as session:
            await session.execute(
                text("DELETE FROM agent_credential_bindings WHERE credential_id = 'cred_foreign'")
            )
            await session.execute(text("DELETE FROM agents WHERE id = 'agnt_owned_by_owner'"))
            await session.commit()
        async with control_db.session() as session:
            await session.execute(
                text("DELETE FROM agent_permission_rules WHERE credential_id = 'cred_foreign'")
            )
            await session.execute(text("DELETE FROM credentials WHERE id = 'cred_foreign'"))
            await session.commit()

    await _cleanup()
    async with control_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO credentials (id, type, name, api_vendor, created_by) "
                "VALUES ('cred_foreign', 'token_value', 'foreign-cred', 'itest-foreign', "
                ":owner) ON CONFLICT DO NOTHING"
            ),
            {"owner": UNRELATED_SUB},
        )
        await session.commit()
    yield
    await _cleanup()


def _foreign_ref_items() -> list[dict[str, object]]:
    return [
        {
            "resource_type": "credential",
            "action": "bind",
            "resource_reference": {"vendor": "itest-foreign"},
        }
    ]


async def test_decide_reference_owner_scope_hides_foreign_credential(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
    seed_foreign_credential: None,
) -> None:
    """A non-admin decider's reference resolution is confined to their owner
    axis: a covering credential owned by someone else does NOT resolve, so the
    approval closes as a DENY with the provision-first reason — never a silent
    grant of another operator's credential."""
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB, reason=None, items=_foreign_ref_items(), identity=filer
    )
    view = await svc.decide(
        filed.id,
        identity=_owner_identity(),
        item_decisions=[{"item_id": filed.items[0].id, "decision": "approved"}],
    )
    assert view.status == "denied"
    assert "No credential covers API" in (view.items[0].decision_reason or "")


async def test_decide_reference_org_admin_resolves_across_owners(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
    seed_foreign_credential: None,
    admin_db: DatabaseSession,
) -> None:
    """org:admin resolves unscoped: the same foreign credential the owner
    couldn't see satisfies the bind when an admin decides."""
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB, reason=None, items=_foreign_ref_items(), identity=filer
    )
    view = await svc.decide(
        filed.id,
        identity=_admin_identity(),
        item_decisions=[{"item_id": filed.items[0].id, "decision": "approved"}],
    )
    assert view.status == "approved"
    assert view.items[0].applied_effects is not None
    assert view.items[0].applied_effects["credential_id"] == "cred_foreign"
    async with admin_db.session() as session:
        bound = await session.execute(
            text(
                "SELECT 1 FROM agent_credential_bindings "
                "WHERE agent_id = :aid AND credential_id = 'cred_foreign'"
            ),
            {"aid": FILER_SUB},
        )
        assert bound.scalar_one_or_none() is not None


async def test_decide_reference_binding_widened_credential_resolves(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
    seed_foreign_credential: None,
    admin_db: DatabaseSession,
) -> None:
    """The binding-widened half of the owner axis: a credential owned by
    someone else still resolves for an owner decider when it is bound to an
    agent that decider owns (the admin-DB push-down list). The decider
    legitimately governs the credential through their agent."""
    async with admin_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO agents (id, name, owner_id, registered_by, status) "
                "VALUES ('agnt_owned_by_owner', 'sibling-agent', :owner, :owner, 'active') "
                "ON CONFLICT DO NOTHING"
            ),
            {"owner": OWNER_SUB},
        )
        await session.execute(
            text(
                "INSERT INTO agent_credential_bindings (id, agent_id, credential_id) "
                "VALUES ('acb_widened_1', 'agnt_owned_by_owner', 'cred_foreign') "
                "ON CONFLICT DO NOTHING"
            )
        )
        await session.commit()

    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB, reason=None, items=_foreign_ref_items(), identity=filer
    )
    view = await svc.decide(
        filed.id,
        identity=_owner_identity(),
        item_decisions=[{"item_id": filed.items[0].id, "decision": "approved"}],
    )
    assert view.status == "approved"
    assert view.items[0].applied_effects is not None
    assert view.items[0].applied_effects["credential_id"] == "cred_foreign"


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

    decide() tolerates same-verdict re-decisions (retry-reconcilable); a genuine
    conflict — requesting DENY for an item that is already APPROVED — surfaces
    as an item-level ItemNotPendingError.
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
    # Rules only enforce on a credential:bind, so amending them is only valid
    # for that item type.
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason=None,
        items=_base_items(),
        identity=filer,
    )
    new_rules = [{"effect": "allow", "methods": ["GET", "POST"]}]
    view = await svc.amend(
        filed.id,
        identity=filer,
        item_amendments=[{"item_id": filed.items[0].id, "rules": new_rules}],
    )
    assert view.items[0].rules == new_rules


async def test_amend_rule_set_id_swaps_policy_carrier(
    svc: AccessRequestService,
    clean_access_requests: None,
    seed_binding: None,
) -> None:
    """Amending a rule_set_id onto a bind detaches its inline rules (and vice
    versa): the stored item never carries both policy carriers."""
    filer = _filer_identity()
    filed = await svc.file(actor_id=FILER_SUB, reason=None, items=_base_items(), identity=filer)
    assert filed.items[0].rules is not None  # the stamped default

    view = await svc.amend(
        filed.id,
        identity=filer,
        item_amendments=[{"item_id": filed.items[0].id, "rule_set_id": "prs_swap_1"}],
    )
    assert view.items[0].rule_set_id == "prs_swap_1"
    assert view.items[0].rules is None

    view = await svc.amend(
        filed.id,
        identity=filer,
        item_amendments=[{"item_id": filed.items[0].id, "rules": _RULES}],
    )
    assert view.items[0].rule_set_id is None
    assert view.items[0].rules == _RULES


async def test_amend_not_pending_raises(
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
        {"resource_type": "credential", "action": "bind", "resource_id": "cred_001"},
        {"resource_type": "credential", "action": "bind", "resource_id": "cred_002"},
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
    admin_db: DatabaseSession,
) -> None:
    """Approving a credential:bind lands BOTH halves of the two-stage effect:
    the control-DB agent permission rules AND the admin-DB agent↔credential
    binding row (a rule-less live bind must be impossible — hard problem 6)."""
    filer = _filer_identity()
    items = [
        {
            "resource_type": "credential",
            "action": "bind",
            "resource_id": "cred_001",
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
    assert approved_item.applied_effects["binding_id"].startswith("acb_")
    assert approved_item.applied_effects["credential_id"] == "cred_001"
    assert approved_item.applied_effects["rules_applied"] == 1
    assert approved_item.applied_effects["already_bound"] is False

    async with admin_db.session() as session:
        binding = await session.execute(
            text(
                "SELECT id FROM agent_credential_bindings "
                "WHERE agent_id = :aid AND credential_id = 'cred_001'"
            ),
            {"aid": FILER_SUB},
        )
        assert binding.scalar_one_or_none() == approved_item.applied_effects["binding_id"]

    async with control_db.session() as session:
        rules = await AgentPermissionRuleRepository.list_rules(session, FILER_SUB, "cred_001")
        assert len(rules) == 1
        assert rules[0].effect == "allow"
        assert rules[0].path == "^/pets"


async def test_approve_credential_bind_already_bound_is_idempotent(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
    admin_db: DatabaseSession,
) -> None:
    """Approving a bind whose binding already exists converges on the existing
    row (ON CONFLICT) and reports already_bound — approval-as-ratification of
    a manually-fulfilled request must not fail or duplicate."""
    async with admin_db.session() as session:
        await session.execute(
            text(
                "INSERT INTO agent_credential_bindings (id, agent_id, credential_id) "
                "VALUES ('acb_preexisting_1', :aid, 'cred_001') ON CONFLICT DO NOTHING"
            ),
            {"aid": FILER_SUB},
        )
        await session.commit()

    filer = _filer_identity()
    filed = await svc.file(actor_id=FILER_SUB, reason=None, items=_base_items(), identity=filer)
    view = await svc.decide(
        filed.id,
        identity=_owner_identity(),
        item_decisions=[{"item_id": filed.items[0].id, "decision": "approved"}],
    )
    assert view.status == "approved"
    effects = view.items[0].applied_effects
    assert effects is not None
    assert effects["already_bound"] is True
    assert effects["binding_id"] == "acb_preexisting_1"

    async with admin_db.session() as session:
        count = await session.execute(
            text(
                "SELECT count(*) FROM agent_credential_bindings "
                "WHERE agent_id = :aid AND credential_id = 'cred_001'"
            ),
            {"aid": FILER_SUB},
        )
        assert count.scalar_one() == 1


# --- cross-DB reconcile (issue #625) ---

SCOPE_GRANT = "owner:toolkits:read"


def _admin_effect_items() -> list[dict[str, object]]:
    """A credential-bind and a scope-grant — both applied as admin-DB effects."""
    return [
        {
            "resource_type": "credential",
            "action": "bind",
            "resource_id": "cred_001",
        },
        {
            "resource_type": "scope",
            "action": "grant",
            "resource_id": SCOPE_GRANT,
        },
    ]


@pytest.fixture()
async def clean_admin_effects(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """Remove any scope-grant rows produced by reconcile tests.

    (The credential-bind side — agent_credential_bindings and
    agent_permission_rules — is cleaned by ``seed_binding``.)
    """

    async def _cleanup() -> None:
        async with admin_db.session() as session:
            await session.execute(
                text("DELETE FROM actor_scope_grants WHERE actor_id = :aid AND scope = :scope"),
                {"aid": FILER_SUB, "scope": SCOPE_GRANT},
            )
            await session.commit()

    await _cleanup()
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


async def _count_credential_bindings(admin_db: DatabaseSession) -> int:
    async with admin_db.session() as session:
        result = await session.execute(
            text(
                "SELECT count(*) FROM agent_credential_bindings "
                "WHERE agent_id = :aid AND credential_id = 'cred_001'"
            ),
            {"aid": FILER_SUB},
        )
        return int(result.scalar_one())


async def _count_control_rules(control_db: DatabaseSession) -> int:
    async with control_db.session() as session:
        result = await session.execute(
            text(
                "SELECT count(*) FROM agent_permission_rules "
                "WHERE agent_id = :aid AND credential_id = 'cred_001'"
            ),
            {"aid": FILER_SUB},
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
    assert items["credential"].status == "approved"
    assert items["scope"].status == "approved"
    # The credential-bind succeeded and is acked; the scope-grant is un-acked.
    assert items["credential"].applied_effects is not None
    assert items["scope"].applied_effects is None

    # Admin DB has exactly the credential binding, no scope grant.
    assert await _count_credential_bindings(admin_db) == 1
    assert await _count_scope_grants(admin_db) == 0

    monkeypatch.setattr(EffectsRepository, "grant_scope_to_actor", original_grant)


async def test_decide_mid_bind_failure_leaves_inert_rules_not_live_bind(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
    clean_admin_effects: None,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The write ORDER is the hard-problem-6 guarantee: rules land in the
    control DB (with the decision) BEFORE the admin binding row. A crash
    between the two leaves inert rules — invisible to enforcement because no
    binding exists — never a live rule-less bind. The retry then converges."""
    filer = _filer_identity()
    filed = await svc.file(actor_id=FILER_SUB, reason=None, items=_base_items(), identity=filer)
    reviewer = _owner_identity()
    decisions = [{"item_id": filed.items[0].id, "decision": "approved"}]

    original_bind = EffectsRepository.bind_agent_to_credential

    async def _boom(*args: object, **kwargs: object) -> tuple[str, bool]:
        raise RuntimeError("simulated crash between prepare and complete")

    monkeypatch.setattr(EffectsRepository, "bind_agent_to_credential", staticmethod(_boom))
    with pytest.raises(AdminEffectReconcileError):
        await svc.decide(filed.id, identity=reviewer, item_decisions=decisions)

    # The crash window: rules committed (inert), NO admin binding.
    assert await _count_control_rules(control_db) == 1
    assert await _count_credential_bindings(admin_db) == 0
    items = await _decided_items(control_db, filed.id)
    assert items["credential"].status == "approved"
    assert items["credential"].applied_effects is None  # un-acked

    # Retry converges: exactly one binding, rules not duplicated.
    monkeypatch.setattr(EffectsRepository, "bind_agent_to_credential", original_bind)
    view = await svc.decide(filed.id, identity=reviewer, item_decisions=decisions)
    assert view.status == "approved"
    assert view.items[0].applied_effects is not None
    assert await _count_credential_bindings(admin_db) == 1
    assert await _count_control_rules(control_db) == 1


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
    assert items["credential"].applied_effects is not None
    assert items["scope"].applied_effects is not None

    # Each admin row exists exactly once (ON CONFLICT idempotency).
    assert await _count_credential_bindings(admin_db) == 1
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
    assert await _count_credential_bindings(admin_db) == 1
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


# --- file-time fulfillability advisory ---


async def _list_events_by_type(admin_db: DatabaseSession, event_type: str) -> list[Event]:
    async with admin_db.session() as session:
        return await EventRepository.list_all(session, event_type=[event_type])


_UNSERVED = "broker.credential_binding_unserved"


async def test_file_emits_unserved_advisory_for_plain_reference_bind(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
    admin_db: DatabaseSession,
) -> None:
    """A plain `credential:bind` by reference with no owned covering credential
    emits a CREDENTIAL_BINDING_UNSERVED advisory (early operator signal that a
    plain approval would deny)."""
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason="bind me to a not-yet-served api",
        items=[
            {
                "resource_type": "credential",
                "action": "bind",
                "resource_reference": {"vendor": "no-such-vendor", "name": "no-such-api"},
            }
        ],
        identity=filer,
    )
    assert filed.status == "pending"  # advisory doesn't block the filing

    events = await _list_events_by_type(admin_db, _UNSERVED)
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


async def test_file_survives_non_string_reference_fields(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
    admin_db: DatabaseSession,
) -> None:
    """Non-string reference values must never escape the advisory (post-commit 500).

    ``resource_reference`` is schema-typed ``dict[str, Any]``, so a caller can
    put an int/list where ``name``/``version`` are expected. The advisory runs
    after the filing has committed; an uncaught TypeError here would fail the
    request *and* skip its CREATE audit record. Values are coerced to strings
    instead.
    """
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason="crafted non-string reference fields",
        items=[
            {
                "resource_type": "credential",
                "action": "bind",
                "resource_reference": {"vendor": "no-such-vendor", "name": 123, "version": 4},
            }
        ],
        identity=filer,
    )
    assert filed.status == "pending"

    events = await _list_events_by_type(admin_db, _UNSERVED)
    matching = [e for e in events if e.data.get("request_id") == filed.id]
    assert len(matching) == 1
    assert matching[0].data["api"] == {
        "vendor": "no-such-vendor",
        "name": "123",
        "version": "4",
    }


async def test_file_skips_unserved_advisory_when_credential_covers_api(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
    control_db: DatabaseSession,
    admin_db: DatabaseSession,
) -> None:
    """When the filer's owner already holds a covering credential, no advisory
    fires — the plain-approve path will resolve cleanly."""
    async with control_db.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO credentials "
                "(id, type, name, api_vendor, api_name, created_by) "
                "VALUES (:id, 'token_value', :name, :vendor, :api_name, :created_by) "
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
    try:
        filer = _filer_identity()
        filed = await svc.file(
            actor_id=FILER_SUB,
            reason="bind me to a served api",
            items=[
                {
                    "resource_type": "credential",
                    "action": "bind",
                    "resource_reference": {"vendor": "servedvendor", "name": "widgets"},
                }
            ],
            identity=filer,
        )
        assert filed.status == "pending"

        events = await _list_events_by_type(admin_db, _UNSERVED)
        matching = [e for e in events if e.data.get("request_id") == filed.id]
        assert matching == []
    finally:
        async with control_db.transaction() as session:
            await session.execute(text("DELETE FROM credentials WHERE id = 'cred_served_001'"))


async def test_file_skips_unserved_advisory_when_request_carries_fulfilment_intent(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
    admin_db: DatabaseSession,
) -> None:
    """A provisioning plan (credential:provision + credential:bind) expects
    nothing to cover the API yet — the advisory must stay silent."""
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason="provision then bind",
        items=[
            {
                "resource_type": "credential",
                "action": "provision",
                "resource_reference": {"vendor": "brandnew", "name": "widgets"},
            },
            {
                "resource_type": "credential",
                "action": "bind",
                "resource_reference": {"vendor": "brandnew", "name": "widgets"},
            },
        ],
        identity=filer,
    )
    assert filed.status == "pending"

    events = await _list_events_by_type(admin_db, _UNSERVED)
    matching = [e for e in events if e.data.get("request_id") == filed.id]
    assert matching == []


async def test_file_skips_unserved_advisory_when_bind_names_credential_by_id(
    svc: AccessRequestService,
    clean_access_requests: None,
    clean_events: None,
    seed_binding: None,
    admin_db: DatabaseSession,
) -> None:
    """A `credential:bind` with an explicit id (not a reference) is not by-name — no advisory."""
    filer = _filer_identity()
    filed = await svc.file(
        actor_id=FILER_SUB,
        reason="bind by id",
        items=_base_items(),
        identity=filer,
    )
    assert filed.status == "pending"

    events = await _list_events_by_type(admin_db, _UNSERVED)
    matching = [e for e in events if e.data.get("request_id") == filed.id]
    assert matching == []
