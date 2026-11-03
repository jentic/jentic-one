"""Edit sessions: changing an existing binding through a connect session.

Drives ``ConnectSessionService`` against the real control and admin databases:
an agent (or its owner) opens an edit of one binding, a human confirms the
revision they reviewed, and the change lands on the binding. The load-bearing
invariant is that an edit session never deletes or unbinds the live
credential, however it ends.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete, select, text, update

from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.audit import AuditEntry
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import AgentCredentialBindingRepository
from jentic_one.control.core.schema.connect_session_outcomes import ConnectSessionOutcome
from jentic_one.control.core.schema.connect_sessions import ConnectSession
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.repos import AgentPermissionRuleRepository, CredentialRepository
from jentic_one.control.repos.connect_session_repo import ConnectSessionRepository
from jentic_one.control.repos.connect_session_revision_repo import (
    ConnectSessionRevisionRepository,
)
from jentic_one.control.repos.permission_rule_set_repo import PermissionRuleSetRepository
from jentic_one.control.repos.prerequisite_repo import PrerequisiteRepository
from jentic_one.control.services.credentials.schemas.credentials import CredentialCreate
from jentic_one.control.services.credentials.schemas.provision import APIReference
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.control.services.integrations import connect_session_service
from jentic_one.control.services.integrations.connect_session_service import (
    ConnectedConfirmResult,
    ConnectSessionService,
    SessionAmend,
)
from jentic_one.control.services.integrations.edits import EditRequest, RuleAdd, rule_to_write
from jentic_one.control.services.integrations.errors import (
    AmendFieldsMismatchError,
    BaselineChangedError,
    ConfirmationForbiddenError,
    EditBindingNotFoundError,
    EditConflictedError,
    EditOpenError,
    InvalidPollTokenError,
    NothingToChangeError,
    RecentlyRejectedError,
    RevisionChangedError,
    RuleSetConfirmForbiddenError,
    RulesRequiredError,
    SessionClosedError,
    TooManyRevisionsError,
    UnknownRuleError,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from jentic_one.shared.models.audit import AuditAction
from jentic_one.shared.models.credentials import CredentialType
from jentic_one.shared.models.events import EventType

pytestmark = pytest.mark.integration

_OWNER_SUB = "usr_cse_owner"
_OTHER_SUB = "usr_cse_other"
_AGENT = "agnt_cse_agent"

_OWNER = Identity(
    sub=_OWNER_SUB,
    email="owner@test.local",
    permissions=["credentials:write", "credentials:read", "agents:write"],
)
_OTHER = Identity(
    sub=_OTHER_SUB,
    email="other@test.local",
    permissions=["credentials:write", "credentials:read", "agents:write"],
)
_AGENT_IDENTITY = Identity(
    sub=_AGENT,
    email="agent@test.local",
    permissions=["credentials:connect", "owner:credentials:read"],
    actor_type=ActorType.AGENT,
    parent_actor_id=_OWNER_SUB,
)

_READ: dict[str, object] = {
    "effect": "allow",
    "methods": ["GET"],
    "path": "/v1/",
    "match_mode": "prefix",
}
_SEND: dict[str, object] = {
    "effect": "allow",
    "methods": ["POST"],
    "path": "/v1/send",
    "match_mode": "exact",
}


async def _wipe(ctx: Context) -> None:
    async with ctx.control_db.session() as session:
        await session.execute(
            delete(ConnectSessionOutcome).where(ConnectSessionOutcome.agent_id == _AGENT)
        )
        await session.execute(delete(ConnectSession).where(ConnectSession.agent_id == _AGENT))
        await session.execute(delete(Credential).where(Credential.created_by == _OWNER_SUB))
        await session.execute(text("DELETE FROM permission_rule_sets WHERE name LIKE 'cse %'"))
        await session.commit()
    async with ctx.admin_db.session() as session:
        await session.execute(
            delete(AgentCredentialBinding).where(AgentCredentialBinding.agent_id == _AGENT)
        )
        await session.execute(delete(Agent).where(Agent.id == _AGENT))
        await session.execute(delete(User).where(User.id.in_([_OWNER_SUB, _OTHER_SUB])))
        await session.commit()


@pytest.fixture()
async def binding(integration_context: Context) -> AsyncGenerator[str, None]:
    """A bearer credential owned by the owner, bound to their agent with one read rule."""
    ctx = integration_context
    await _wipe(ctx)
    created = await CredentialService(ctx).create(
        CredentialCreate(
            type=CredentialType.BEARER_TOKEN,
            name="cse credential",
            api=APIReference(vendor="test-vendor", name="test-api", version="v1"),
            token="sk-cse",
        ),
        identity=_OWNER,
    )
    async with ctx.admin_db.transaction() as session:
        for sub in (_OWNER_SUB, _OTHER_SUB):
            session.add(User(id=sub, email=f"{sub}@test.local", first_name="T", last_name="U"))
        await session.flush()
        session.add(
            Agent(
                id=_AGENT,
                name="cse",
                owner_id=_OWNER_SUB,
                registered_by=_OWNER_SUB,
                status="approved",
            )
        )
        await session.flush()
        await AgentCredentialBindingRepository.bind(
            session, agent_id=_AGENT, credential_id=created.credential_id, created_by=_OWNER_SUB
        )
    await CredentialService(ctx).replace_agent_permissions(
        created.credential_id, _AGENT, [_READ], identity=_OWNER
    )
    yield created.credential_id
    await _wipe(ctx)


async def _rules(ctx: Context, credential_id: str) -> list[tuple[str, str | None]]:
    async with ctx.control_db.session() as session:
        rules = await AgentPermissionRuleRepository.list_rules(session, _AGENT, credential_id)
    return [(r.effect, r.path) for r in rules if not r.is_system]


async def _rule_ids(ctx: Context, credential_id: str) -> list[str]:
    async with ctx.control_db.session() as session:
        rules = await AgentPermissionRuleRepository.list_rules(session, _AGENT, credential_id)
    return [r.id for r in rules if not r.is_system]


async def _binding_row(ctx: Context, credential_id: str) -> Any:
    async with ctx.admin_db.session() as session:
        return await PrerequisiteRepository.get_agent_credential_binding(
            session, agent_id=_AGENT, credential_id=credential_id
        )


async def _credential_exists(ctx: Context, credential_id: str) -> bool:
    async with ctx.control_db.session() as session:
        return await CredentialRepository.get_by_id(session, credential_id) is not None


async def _begin(
    ctx: Context, credential_id: str, request: EditRequest, identity: Identity = _AGENT_IDENTITY
) -> Any:
    return await ConnectSessionService(ctx).create_edit_session(
        credential_id=credential_id, agent_id=_AGENT, identity=identity, request=request
    )


async def test_agent_edit_applies_the_reviewers_rules(
    integration_context: Context, binding: str
) -> None:
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await _begin(
        ctx, binding, EditRequest(add=[RuleAdd(_SEND)], reason="needs to send replies")
    )
    assert created.revision_id.startswith("csr_")
    assert created.resolved_flow == "edit"

    review = await svc.get_review_data(created.session_id, poll_token=None, identity=_OWNER)
    assert review.edit is not None
    assert review.can_confirm is True
    assert review.edit.revision_id == created.revision_id
    assert [r["path"] for r in review.edit.current["rules"]] == ["/v1/"]
    assert [r["path"] for r in review.edit.result["rules"]] == ["/v1/", "/v1/send"]
    assert review.edit.widens_access is True
    # Nothing changes until a human confirms.
    assert await _rules(ctx, binding) == [("allow", "/v1/")]

    # The reviewer narrows the ask: only their submission is applied.
    narrowed = _SEND | {"methods": ["POST"], "path": "/v1/send/reply"}
    result = await svc.confirm_edit(
        created.session_id,
        poll_token=None,
        identity=_OWNER,
        revision_id=created.revision_id,
        permission_rules=[_READ, narrowed],
        resume=False,
    )
    assert isinstance(result, ConnectedConfirmResult) and result.credential_id == binding
    assert await _rules(ctx, binding) == [("allow", "/v1/"), ("allow", "/v1/send/reply")]

    status = await svc.get_status(
        created.session_id, poll_token=created.poll_token, identity=_AGENT_IDENTITY
    )
    assert (status.status, status.credential_id) == ("applied", binding)
    assert await _credential_exists(ctx, binding)
    assert await _binding_row(ctx, binding) is not None


async def test_edit_requests_that_do_not_fit_the_binding_are_refused(
    integration_context: Context, binding: str
) -> None:
    ctx = integration_context
    with pytest.raises(NothingToChangeError):
        await _begin(ctx, binding, EditRequest(resume=True))
    with pytest.raises(NothingToChangeError):
        await _begin(ctx, binding, EditRequest(set_rules=[_READ]))
    with pytest.raises(UnknownRuleError):
        await _begin(ctx, binding, EditRequest(remove=["apr_not_here"]))
    with pytest.raises(EditBindingNotFoundError):
        await _begin(ctx, "cred_not_bound", EditRequest(add=[RuleAdd(_SEND)]))
    # Another human may not edit the owner's agent's binding.
    with pytest.raises(EditBindingNotFoundError):
        await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]), identity=_OTHER)

    first = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))
    with pytest.raises(EditOpenError) as err:
        await _begin(ctx, binding, EditRequest(remove=await _rule_ids(ctx, binding)))
    assert err.value.session_id == first.session_id


@pytest.mark.parametrize("how", ["cancel", "reject", "expire"])
async def test_an_ended_edit_never_touches_the_credential_or_binding(
    integration_context: Context, binding: str, how: str
) -> None:
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))
    if how == "cancel":
        await svc.cancel_session(
            created.session_id, poll_token=created.poll_token, identity=_AGENT_IDENTITY
        )
    elif how == "reject":
        await svc.reject_session(created.session_id, identity=_OWNER)
    else:
        async with ctx.control_db.transaction() as session:
            await session.execute(
                update(ConnectSession)
                .where(ConnectSession.id == created.session_id)
                .values(created_at=datetime(2020, 1, 1, tzinfo=UTC))
            )
        assert await svc.expire_stale_sessions() >= 1

    assert await _credential_exists(ctx, binding)
    assert await _binding_row(ctx, binding) is not None
    assert await _rules(ctx, binding) == [("allow", "/v1/")]
    async with ctx.control_db.session() as session:
        assert await ConnectSessionRepository.get_by_id(session, created.session_id) is None
    status = await svc.get_status(
        created.session_id, poll_token=created.poll_token, identity=_AGENT_IDENTITY
    )
    assert status.status in ("failed", "expired")
    # The binding is free for a new edit, however the last one ended (no cooldown by default).
    await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))


async def test_confirm_must_name_the_current_revision(
    integration_context: Context, binding: str
) -> None:
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))
    with pytest.raises(RevisionChangedError) as stale:
        await svc.confirm_edit(
            created.session_id,
            poll_token=None,
            identity=_OWNER,
            revision_id="csr_stale",
            permission_rules=[_READ, _SEND],
            resume=False,
        )
    assert stale.value.current_revision_number == 1
    assert "revision 1 is now the current one" in str(stale.value)
    assert await _rules(ctx, binding) == [("allow", "/v1/")]


async def test_an_approver_cannot_strip_every_rule(
    integration_context: Context, binding: str
) -> None:
    """An empty rule list is default deny: the agent could call nothing.

    The console won't offer it; the API refuses it too. A binding that has no
    rules to begin with stays editable.
    """
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))
    with pytest.raises(RulesRequiredError):
        await svc.confirm_edit(
            created.session_id,
            poll_token=None,
            identity=_OWNER,
            revision_id=created.revision_id,
            permission_rules=[],
            resume=False,
        )
    assert await _rules(ctx, binding) == [("allow", "/v1/")]

    # A binding that holds no rules to begin with stays editable: the same
    # empty confirm goes through, because it strips nothing.
    async with ctx.control_db.transaction() as session:
        await AgentPermissionRuleRepository.replace_user_rules(
            session, _AGENT, binding, [], created_by=_OWNER.sub
        )
    await svc.rebase_session(created.session_id, poll_token=None, identity=_OWNER)
    rebased = await svc.get_review_data(created.session_id, poll_token=None, identity=_OWNER)
    assert rebased.edit is not None
    await svc.confirm_edit(
        created.session_id,
        poll_token=None,
        identity=_OWNER,
        revision_id=rebased.edit.revision_id,
        permission_rules=[],
        resume=False,
    )
    assert await _rules(ctx, binding) == []


async def test_an_amend_that_changes_nothing_keeps_the_current_revision(
    integration_context: Context, binding: str
) -> None:
    """A reviewer saving the rules unchanged writes no empty revision."""
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))
    review = await svc.get_review_data(created.session_id, poll_token=None, identity=_OWNER)
    assert review.edit is not None
    unchanged = await svc.amend_session(
        created.session_id,
        identity=_OWNER,
        amend=SessionAmend(
            fields=frozenset({"rules"}),
            rules=EditRequest(set_rules=[rule_to_write(r) for r in review.edit.result["rules"]]),
        ),
    )
    assert (unchanged.revision_id, unchanged.revision_number) == (created.revision_id, 1)


async def test_the_review_shows_when_this_binding_s_edit_was_last_rejected(
    integration_context: Context, binding: str
) -> None:
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))
    first = await svc.get_review_data(created.session_id, poll_token=None, identity=_OWNER)
    assert first.last_rejected_at is None
    await svc.reject_session(created.session_id, identity=_OWNER)
    again = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))
    review = await svc.get_review_data(again.session_id, poll_token=None, identity=_OWNER)
    assert review.last_rejected_at is not None


async def test_agents_never_confirm_an_edit(integration_context: Context, binding: str) -> None:
    ctx = integration_context
    created = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))
    with pytest.raises(ConfirmationForbiddenError):
        await ConnectSessionService(ctx).confirm_edit(
            created.session_id,
            poll_token=created.poll_token,
            identity=_AGENT_IDENTITY,
            revision_id=created.revision_id,
            permission_rules=[_READ, _SEND],
            resume=False,
        )


async def test_a_direct_rule_change_rebases_the_open_edit(
    integration_context: Context, binding: str
) -> None:
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    [read_id] = await _rule_ids(ctx, binding)
    created = await _begin(
        ctx, binding, EditRequest(add=[RuleAdd(_SEND, before=read_id)], remove=[read_id])
    )
    # The owner replaces the rules directly while the edit is open.
    await CredentialService(ctx).replace_agent_permissions(
        binding, _AGENT, [_READ | {"path": "/v2/"}], identity=_OWNER
    )
    with pytest.raises(BaselineChangedError) as err:
        await svc.confirm_edit(
            created.session_id,
            poll_token=None,
            identity=_OWNER,
            revision_id=created.revision_id,
            permission_rules=[_SEND],
            resume=False,
        )
    rebased_id = err.value.current_revision_id
    assert rebased_id != created.revision_id
    async with ctx.control_db.session() as session:
        revisions = await ConnectSessionRevisionRepository.list_for_session(
            session, created.session_id
        )
    rebased = revisions[-1]
    assert (rebased.id, rebased.number) == (rebased_id, 2)
    assert rebased.proposal["dropped"] == [read_id, read_id]
    # The rule it removes and inserts before is gone: a conflict, not a silent drop.
    assert rebased.proposal["conflicts"] == [{"kind": "missing_rule", "rule_id": read_id}]
    assert [r["path"] for r in rebased.result["rules"]] == ["/v2/", "/v1/send"]

    await svc.confirm_edit(
        created.session_id,
        poll_token=None,
        identity=_OWNER,
        revision_id=rebased_id,
        permission_rules=[_READ | {"path": "/v2/"}, _SEND],
        resume=False,
        resolve_conflicts=True,
    )
    assert await _rules(ctx, binding) == [("allow", "/v2/"), ("allow", "/v1/send")]


async def test_resuming_a_suspended_binding(integration_context: Context, binding: str) -> None:
    ctx = integration_context
    async with ctx.admin_db.transaction() as session:
        await AgentCredentialBindingRepository.set_suspended(
            session, agent_id=_AGENT, credential_id=binding, suspended=True
        )
    # Suspension hides the binding from its agent, so only a human can ask.
    with pytest.raises(EditBindingNotFoundError):
        await _begin(ctx, binding, EditRequest(resume=True))
    created = await _begin(ctx, binding, EditRequest(resume=True), identity=_OWNER)
    await ConnectSessionService(ctx).confirm_edit(
        created.session_id,
        poll_token=None,
        identity=_OWNER,
        revision_id=created.revision_id,
        permission_rules=[_READ],
        resume=True,
    )
    row = await _binding_row(ctx, binding)
    assert row.suspended is False


async def _attach_rule_set(ctx: Context, credential_id: str, *, curated: bool) -> str:
    owner = (
        Identity(sub=_OTHER_SUB, email="other@test.local", permissions=["org:admin"])
        if curated
        else _OTHER
    )
    svc = CredentialService(ctx)
    rule_set, _ = await svc.create_rule_set(
        name=f"cse {'curated' if curated else 'shared'}",
        description=None,
        rules=[_READ],
        identity=owner,
    )
    async with ctx.admin_db.transaction() as session:
        await PrerequisiteRepository.set_binding_rule_set(
            session, agent_id=_AGENT, credential_id=credential_id, rule_set_id=rule_set.id
        )
    return rule_set.id


async def test_a_shared_rule_set_edit_names_every_agent_it_reaches(
    integration_context: Context, binding: str
) -> None:
    """One write to a shared set reaches every binding that points at it.

    Computed from the credential alone, the affected-agent list shows a
    reviewer nobody while they approve a change to other agents' policy on
    other credentials.
    """
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    rule_set_id = await _attach_rule_set(ctx, binding, curated=False)
    # A second agent, on its own credential, governed by the same set.
    other_credential = await CredentialService(ctx).create(
        CredentialCreate(
            type=CredentialType.BEARER_TOKEN,
            name="cse second credential",
            api=APIReference(vendor="acme-com", name="widgets", version="v1"),
            token="sk-second",
        ),
        identity=_OWNER,
    )
    async with ctx.admin_db.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO agents (id, name, registered_by, owner_id, status) "
                "VALUES ('agnt_cse_shared', 'shared', :u, :u, 'approved')"
            ),
            {"u": _OWNER_SUB},
        )
        await AgentCredentialBindingRepository.bind(
            session,
            agent_id="agnt_cse_shared",
            credential_id=other_credential.credential_id,
            created_by=_OWNER_SUB,
        )
        await PrerequisiteRepository.set_binding_rule_set(
            session,
            agent_id="agnt_cse_shared",
            credential_id=other_credential.credential_id,
            rule_set_id=rule_set_id,
        )

    created = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))
    try:
        review = await svc.get_review_data(created.session_id, poll_token=None, identity=_OWNER)
        assert review.edit is not None and review.edit.result["target"] == "rule_set"
        assert [a.agent_id for a in review.edit.other_bound_agents] == ["agnt_cse_shared"]
        assert review.edit.other_bound_agents_truncated is False
    finally:
        # Outside the fixture's own wipe, so a failing assert must not leave
        # the row for the next test in this module.
        async with ctx.admin_db.transaction() as session:
            await session.execute(
                text("DELETE FROM agent_credential_bindings WHERE agent_id = 'agnt_cse_shared'")
            )
            await session.execute(text("DELETE FROM agents WHERE id = 'agnt_cse_shared'"))


async def test_the_review_names_no_agent_the_reader_may_not_see(
    integration_context: Context, binding: str
) -> None:
    """A review never introduces a stranger's agent.

    An admin may bind one user's credential to another's agent; the second
    user then read the first's agent id and name off the review, where the
    credential's own bound-agent list refuses them.
    """
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    async with ctx.admin_db.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, email, first_name, last_name) "
                "VALUES (:id, :email, 'Mallory', 'Test')"
            ),
            {"id": "usr_cse_stranger", "email": "stranger@cse.test"},
        )
        await session.execute(
            text(
                "INSERT INTO agents (id, name, registered_by, owner_id, status) "
                "VALUES ('agnt_cse_foreign', 'Foreign', :u, :u, 'approved')"
            ),
            {"u": "usr_cse_stranger"},
        )
        await AgentCredentialBindingRepository.bind(
            session,
            agent_id="agnt_cse_foreign",
            credential_id=binding,
            created_by=_OWNER_SUB,
        )
    created = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))
    try:
        # The credential's creator sees every agent it reaches.
        mine = await svc.get_review_data(created.session_id, poll_token=None, identity=_OWNER)
        assert mine.edit is not None
        assert [a.agent_id for a in mine.edit.other_bound_agents] == ["agnt_cse_foreign"]
        # The agent's own poll token does not widen what the review names.
        theirs = await svc.get_review_data(
            created.session_id, poll_token=created.poll_token, identity=_AGENT_IDENTITY
        )
        assert theirs.edit is not None and theirs.edit.other_bound_agents == []
    finally:
        async with ctx.admin_db.transaction() as session:
            await session.execute(
                text("DELETE FROM agent_credential_bindings WHERE agent_id = 'agnt_cse_foreign'")
            )
            await session.execute(text("DELETE FROM agents WHERE id = 'agnt_cse_foreign'"))
            await session.execute(text("DELETE FROM users WHERE id = 'usr_cse_stranger'"))


async def test_can_confirm_says_what_the_confirm_will_do(
    integration_context: Context, binding: str
) -> None:
    """The review must not offer an approval the confirm then refuses.

    Answering on the agent and the state alone, ``can_confirm`` shows a
    reviewer an Approve that 403s on a rule set, or on a credential that is
    not theirs.
    """
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    await _attach_rule_set(ctx, binding, curated=False)
    created = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))

    # The set belongs to someone else, so this reviewer cannot confirm.
    review = await svc.get_review_data(created.session_id, poll_token=None, identity=_OWNER)
    assert review.can_confirm is False
    with pytest.raises(RuleSetConfirmForbiddenError):
        await svc.confirm_edit(
            created.session_id,
            poll_token=None,
            identity=_OWNER,
            revision_id=review.revision_id or "",
            permission_rules=[_READ, _SEND],
            resume=False,
        )
    # An org admin may change any set, and is offered the approval.
    admin = Identity(sub="usr_cse_admin", email="admin@test.local", permissions=["org:admin"])
    as_admin = await svc.get_review_data(created.session_id, poll_token=None, identity=admin)
    assert as_admin.can_confirm is True


async def test_a_shared_rule_set_edit_needs_the_sets_owner(
    integration_context: Context, binding: str
) -> None:
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    rule_set_id = await _attach_rule_set(ctx, binding, curated=False)
    created = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))
    review = await svc.get_review_data(created.session_id, poll_token=None, identity=_OWNER)
    assert review.edit is not None and review.edit.result["target"] == "rule_set"

    with pytest.raises(RuleSetConfirmForbiddenError):
        await svc.confirm_edit(
            created.session_id,
            poll_token=None,
            identity=_OWNER,
            revision_id=created.revision_id,
            permission_rules=[_READ, _SEND],
            resume=False,
        )
    admin = Identity(sub="usr_cse_admin", email="admin@test.local", permissions=["org:admin"])
    await svc.confirm_edit(
        created.session_id,
        poll_token=None,
        identity=admin,
        revision_id=created.revision_id,
        permission_rules=[_READ, _SEND],
        resume=False,
    )
    _, set_rules, _ = await CredentialService(ctx).get_rule_set(rule_set_id, identity=admin)
    assert [r.path for r in set_rules] == ["/v1/", "/v1/send"]
    # The binding's own rules are untouched; the set still applies.
    assert await _rules(ctx, binding) == [("allow", "/v1/")]
    assert (await _binding_row(ctx, binding)).rule_set_id == rule_set_id


async def test_a_curated_rule_set_is_copied_never_changed(
    integration_context: Context, binding: str
) -> None:
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    rule_set_id = await _attach_rule_set(ctx, binding, curated=True)
    created = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))
    await svc.confirm_edit(
        created.session_id,
        poll_token=None,
        identity=_OWNER,
        revision_id=created.revision_id,
        permission_rules=[_READ, _SEND],
        resume=False,
    )
    admin = Identity(sub="usr_cse_admin", email="admin@test.local", permissions=["org:admin"])
    _, set_rules, _ = await CredentialService(ctx).get_rule_set(rule_set_id, identity=admin)
    assert [r.path for r in set_rules] == ["/v1/"]
    assert await _rules(ctx, binding) == [("allow", "/v1/"), ("allow", "/v1/send")]
    assert (await _binding_row(ctx, binding)).rule_set_id is None


# --- amend ---


async def test_amending_an_edit_makes_a_new_revision_the_confirm_must_name(
    integration_context: Context, binding: str
) -> None:
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)], reason="first"))
    amended = await svc.amend_session(
        created.session_id,
        identity=_AGENT_IDENTITY,
        amend=SessionAmend(
            fields=frozenset({"rules"}),
            rules=EditRequest(add=[RuleAdd(_SEND), RuleAdd(_SEND | {"path": "/v1/label"})]),
        ),
    )
    assert (amended.session_id, amended.revision_number) == (created.session_id, 2)
    review = await svc.get_review_data(created.session_id, poll_token=None, identity=_OWNER)
    assert review.revision_id == amended.revision_id
    assert review.reason == "first"  # an omitted field keeps the current value
    assert review.edit is not None
    assert [r["path"] for r in review.edit.result["rules"]] == ["/v1/", "/v1/send", "/v1/label"]

    # Approving what the reviewer saw before the amend is refused.
    with pytest.raises(RevisionChangedError):
        await svc.confirm_edit(
            created.session_id,
            poll_token=None,
            identity=_OWNER,
            revision_id=created.revision_id,
            permission_rules=[_READ, _SEND],
            resume=False,
        )
    await svc.confirm_edit(
        created.session_id,
        poll_token=None,
        identity=_OWNER,
        revision_id=amended.revision_id,
        permission_rules=[_READ, _SEND],
        resume=False,
    )
    # A closed session can never be amended.
    with pytest.raises(SessionClosedError):
        await svc.amend_session(
            created.session_id,
            identity=_AGENT_IDENTITY,
            amend=SessionAmend(fields=frozenset({"reason"}), reason="later"),
        )


async def test_only_the_initiator_or_its_reviewer_amends_and_only_with_the_sessions_fields(
    integration_context: Context, binding: str
) -> None:
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))
    with pytest.raises(InvalidPollTokenError):
        await svc.amend_session(
            created.session_id,
            identity=_OTHER,
            amend=SessionAmend(fields=frozenset({"reason"}), reason="not mine"),
        )
    with pytest.raises(AmendFieldsMismatchError):
        await svc.amend_session(
            created.session_id,
            identity=_AGENT_IDENTITY,
            amend=SessionAmend(fields=frozenset({"requested_scopes"}), requested_scopes=["x"]),
        )
    with pytest.raises(NothingToChangeError):
        await svc.amend_session(
            created.session_id,
            identity=_AGENT_IDENTITY,
            amend=SessionAmend(fields=frozenset({"rules"}), rules=EditRequest()),
        )


async def test_a_rejected_edit_holds_back_the_next_one_for_the_cooldown(
    integration_context: Context, binding: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = integration_context
    # The cooldown is the same setting as for connect requests, off by default.
    monkeypatch.setattr(ctx.config.control.connect, "rejection_cooldown_hours", 24)
    svc = ConnectSessionService(ctx)
    created = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))
    await svc.reject_session(created.session_id, identity=_OWNER)
    with pytest.raises(RecentlyRejectedError):
        await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))

    # The hold is on the agent: its owner can still ask for the change.
    by_owner = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]), identity=_OWNER)
    await svc.cancel_session(by_owner.session_id, poll_token=by_owner.poll_token, identity=_OWNER)

    monkeypatch.setattr(ctx.config.control.connect, "rejection_cooldown_hours", 0)
    await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))


async def test_a_cancelled_edit_does_not_hold_back_the_next_one(
    integration_context: Context, binding: str
) -> None:
    ctx = integration_context
    created = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))
    await ConnectSessionService(ctx).cancel_session(
        created.session_id, poll_token=created.poll_token, identity=_AGENT_IDENTITY
    )
    await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))


async def _apply(ctx: Context, credential_id: str, request: EditRequest) -> Any:
    return await ConnectSessionService(ctx).create_edit_session(
        credential_id=credential_id,
        agent_id=_AGENT,
        identity=_OWNER,
        request=request,
        apply=True,
    )


async def test_a_human_applies_their_own_change_at_once(
    integration_context: Context, binding: str
) -> None:
    ctx = integration_context
    applied = await _apply(ctx, binding, EditRequest(set_rules=[_SEND]))
    assert applied.applied and applied.challenge is None
    assert await _rules(ctx, binding) == [("allow", "/v1/send")]
    # Nothing is left open, and the session reads as applied.
    async with ctx.control_db.session() as session:
        assert (
            await ConnectSessionRepository.get_open_edit(
                session, agent_id=_AGENT, credential_id=binding, initiator_actor_id=_OWNER_SUB
            )
            is None
        )
    status = await ConnectSessionService(ctx).get_status(
        applied.session_id, poll_token=applied.poll_token, identity=_OWNER
    )
    assert status.status == "applied"


async def test_an_agent_never_applies_its_own_change(
    integration_context: Context, binding: str
) -> None:
    with pytest.raises(ConfirmationForbiddenError):
        await ConnectSessionService(integration_context).create_edit_session(
            credential_id=binding,
            agent_id=_AGENT,
            identity=_AGENT_IDENTITY,
            request=EditRequest(add=[RuleAdd(_SEND)]),
            apply=True,
        )


async def test_an_open_edit_merges_onto_a_human_change_that_doesnt_touch_it(
    integration_context: Context, binding: str
) -> None:
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))
    # The owner changes the rules while the agent's request is open.
    await _apply(ctx, binding, EditRequest(set_rules=[_READ | {"path": "/v2/"}]))

    # The read says so and writes nothing; the rebase is asked for.
    stale = await svc.get_review_data(created.session_id, poll_token=None, identity=_OWNER)
    assert stale.stale is True and stale.revision_id == created.revision_id
    status = await svc.get_status(
        created.session_id, poll_token=created.poll_token, identity=_AGENT_IDENTITY
    )
    assert (status.status, status.stale) == ("pending", True)

    await svc.rebase_session(created.session_id, poll_token=None, identity=_OWNER)
    review = await svc.get_review_data(created.session_id, poll_token=None, identity=_OWNER)
    assert review.edit is not None and review.edit.conflicts == []
    assert review.stale is False
    assert review.revision_id != created.revision_id
    assert [r["path"] for r in review.edit.result["rules"]] == ["/v2/", "/v1/send"]
    status = await svc.get_status(
        created.session_id, poll_token=created.poll_token, identity=_AGENT_IDENTITY
    )
    assert (status.status, status.error_code) == ("pending", None)

    await svc.confirm_edit(
        created.session_id,
        poll_token=None,
        identity=_OWNER,
        revision_id=review.edit.revision_id,
        permission_rules=[_READ | {"path": "/v2/"}, _SEND],
        resume=False,
    )
    assert await _rules(ctx, binding) == [("allow", "/v2/"), ("allow", "/v1/send")]


async def test_an_open_edit_the_human_change_breaks_is_conflicted_until_resolved(
    integration_context: Context, binding: str
) -> None:
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    [read_id] = await _rule_ids(ctx, binding)
    created = await _begin(ctx, binding, EditRequest(remove=[read_id], add=[RuleAdd(_SEND)]))
    # The owner replaces the rule the agent's request removes.
    await _apply(ctx, binding, EditRequest(set_rules=[_READ | {"path": "/v2/"}]))

    # Stale first: the conflict is known once the ask is resolved onto the
    # binding as it is now, which the reader asks for.
    status = await svc.get_status(
        created.session_id, poll_token=created.poll_token, identity=_AGENT_IDENTITY
    )
    assert (status.status, status.stale) == ("pending", True)
    await svc.rebase_session(created.session_id, poll_token=None, identity=_OWNER)
    status = await svc.get_status(
        created.session_id, poll_token=created.poll_token, identity=_AGENT_IDENTITY
    )
    assert (status.status, status.error_code) == ("pending", "conflicted")
    review = await svc.get_review_data(created.session_id, poll_token=None, identity=_OWNER)
    assert review.edit is not None
    assert review.edit.conflicts == [{"kind": "missing_rule", "rule_id": read_id}]

    with pytest.raises(EditConflictedError) as err:
        await svc.confirm_edit(
            created.session_id,
            poll_token=None,
            identity=_OWNER,
            revision_id=review.edit.revision_id,
            permission_rules=[_SEND],
            resume=False,
        )
    assert err.value.conflicts == review.edit.conflicts

    # The agent resolves it: amending against the binding as it is now.
    amended = await svc.amend_session(
        created.session_id,
        identity=_AGENT_IDENTITY,
        amend=SessionAmend(fields=frozenset({"rules"}), rules=EditRequest(add=[RuleAdd(_SEND)])),
    )
    review = await svc.get_review_data(created.session_id, poll_token=None, identity=_OWNER)
    assert review.edit is not None and review.edit.conflicts == []
    assert review.revision_id == amended.revision_id


async def test_a_full_replacement_follows_the_binding_without_a_conflict(
    integration_context: Context, binding: str
) -> None:
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await _begin(ctx, binding, EditRequest(set_rules=[_SEND]))
    # The rules change under it: the request still applies, so it just rebases.
    await _apply(ctx, binding, EditRequest(add=[RuleAdd(_READ | {"path": "/v2/"})]))
    await svc.rebase_session(created.session_id, poll_token=None, identity=_OWNER)
    review = await svc.get_review_data(created.session_id, poll_token=None, identity=_OWNER)
    assert review.edit is not None and review.edit.conflicts == []
    assert review.edit.revision_id != created.revision_id
    # The diff is against today's rules, which the replacement drops.
    assert [r["path"] for r in review.edit.current["rules"]] == ["/v1/", "/v2/"]
    assert [r["path"] for r in review.edit.result["rules"]] == ["/v1/send"]

    await svc.confirm_edit(
        created.session_id,
        poll_token=None,
        identity=_OWNER,
        revision_id=review.edit.revision_id,
        permission_rules=[_SEND],
        resume=False,
    )
    assert await _rules(ctx, binding) == [("allow", "/v1/send")]


async def test_the_reviewer_edits_the_request_as_a_revision_of_their_own(
    integration_context: Context, binding: str
) -> None:
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))
    review = await svc.get_review_data(created.session_id, poll_token=None, identity=_OWNER)
    assert review.edit is not None
    agent_rules = [rule_to_write(r) for r in review.edit.result["rules"]]

    # Someone who can't approve it can't edit it either.
    with pytest.raises(InvalidPollTokenError):
        await svc.amend_session(
            created.session_id,
            identity=_OTHER,
            amend=SessionAmend(fields=frozenset({"rules"}), rules=EditRequest(set_rules=[_READ])),
        )

    # The owner adds a rule of their own to the request.
    extra = _READ | {"path": "/v2/", "comment": "Also the new API"}
    revised = await svc.amend_session(
        created.session_id,
        identity=_OWNER,
        amend=SessionAmend(
            fields=frozenset({"rules"}), rules=EditRequest(set_rules=[*agent_rules, extra])
        ),
    )
    assert revised.revision_number == 2
    review = await svc.get_review_data(created.session_id, poll_token=None, identity=_OWNER)
    assert review.edit is not None
    assert [(r["path"], r["added_by"]) for r in review.edit.result["rules"]] == [
        ("/v1/", None),
        ("/v1/send", _AGENT),
        ("/v2/", _OWNER_SUB),
    ]
    async with ctx.control_db.session() as session:
        revisions = await ConnectSessionRevisionRepository.list_for_session(
            session, created.session_id
        )
    assert [r.created_by for r in revisions] == [_AGENT, _OWNER_SUB]
    # The reviewer's change is on the rail too, named as theirs: an approver
    # watching the request has to see that it moved.
    async with ctx.admin_db.session() as session:
        events = [
            e
            for e in (await session.execute(select(Event))).scalars().all()
            if e.type == EventType.CONNECT_SESSION_AMENDED
            and (e.data or {}).get("session_id") == created.session_id
        ]
    assert [(e.actor_id, (e.data or {})["revision_id"]) for e in events] == [
        (_OWNER_SUB, revised.revision_id)
    ]

    await svc.confirm_edit(
        created.session_id,
        poll_token=None,
        identity=_OWNER,
        revision_id=revised.revision_id,
        permission_rules=[rule_to_write(r) for r in review.edit.result["rules"]],
        resume=False,
    )
    assert await _rules(ctx, binding) == [
        ("allow", "/v1/"),
        ("allow", "/v1/send"),
        ("allow", "/v2/"),
    ]


async def test_a_full_revision_chain_grows_no_further_however_it_is_touched(
    integration_context: Context, binding: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At the cap the chain stops growing, whichever caller touches it.

    An amend resolves the ask onto the live binding before it writes, and so
    appends a revision of its own: asked for room only afterwards, it could
    grow the chain past the ceiling and still refuse the caller. A caller's
    ``:rebase`` is refused for the same reason, and the rebase a confirm does
    for itself stops at the head it finds rather than appending past it.
    """
    monkeypatch.setattr(connect_session_service, "_MAX_SESSION_REVISIONS", 2)
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))
    second = await svc.amend_session(
        created.session_id,
        identity=_AGENT_IDENTITY,
        amend=SessionAmend(fields=frozenset({"reason"}), reason="take 2"),
    )
    # The binding moves under the full chain, so a rebase has work to do.
    await CredentialService(ctx).replace_agent_permissions(
        binding, _AGENT, [_READ | {"path": "/v2/"}], identity=_OWNER
    )

    with pytest.raises(TooManyRevisionsError) as capped:
        await svc.amend_session(
            created.session_id,
            identity=_AGENT_IDENTITY,
            amend=SessionAmend(fields=frozenset({"reason"}), reason="once more"),
        )
    assert capped.value.limit == 2
    with pytest.raises(TooManyRevisionsError):
        await svc.rebase_session(created.session_id, poll_token=None, identity=_OWNER)
    # The confirm's own rebase keeps the head it has: the ask is stale and its
    # approver has to ask again, which is what the cap is for.
    with pytest.raises(BaselineChangedError) as stale:
        await svc.confirm_edit(
            created.session_id,
            poll_token=None,
            identity=_OWNER,
            revision_id=second.revision_id,
            permission_rules=[_SEND],
            resume=False,
        )
    assert stale.value.current_revision_id == second.revision_id

    async with ctx.control_db.session() as session:
        revisions = await ConnectSessionRevisionRepository.list_for_session(
            session, created.session_id
        )
    assert [r.number for r in revisions] == [1, 2]


async def test_a_rule_set_edit_names_no_agent_bound_on_a_credential_the_reader_cannot_read(
    integration_context: Context, binding: str
) -> None:
    """Owning the edited credential confers no sight of the others the set governs.

    The shared-set reach query returns bindings on every credential, so the
    rows it returns are filtered by what the reader may read, exactly as the
    credential's own bound-agent list is.
    """
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    rule_set_id = await _attach_rule_set(ctx, binding, curated=False)
    # A stranger's agent, on the stranger's own credential, under the same set.
    theirs = await CredentialService(ctx).create(
        CredentialCreate(
            type=CredentialType.BEARER_TOKEN,
            name="cse stranger credential",
            api=APIReference(vendor="acme-com", name="widgets", version="v1"),
            token="sk-stranger",
        ),
        identity=_OTHER,
    )
    async with ctx.admin_db.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO agents (id, name, registered_by, owner_id, status) "
                "VALUES ('agnt_cse_theirs', 'Theirs', :u, :u, 'approved')"
            ),
            {"u": _OTHER_SUB},
        )
        await AgentCredentialBindingRepository.bind(
            session,
            agent_id="agnt_cse_theirs",
            credential_id=theirs.credential_id,
            created_by=_OTHER_SUB,
        )
        await PrerequisiteRepository.set_binding_rule_set(
            session,
            agent_id="agnt_cse_theirs",
            credential_id=theirs.credential_id,
            rule_set_id=rule_set_id,
        )
    created = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))
    try:
        review = await svc.get_review_data(created.session_id, poll_token=None, identity=_OWNER)
        assert review.edit is not None and review.edit.result["target"] == "rule_set"
        assert review.edit.other_bound_agents == []
    finally:
        async with ctx.admin_db.transaction() as session:
            await session.execute(
                text("DELETE FROM agent_credential_bindings WHERE agent_id = 'agnt_cse_theirs'")
            )
            await session.execute(text("DELETE FROM agents WHERE id = 'agnt_cse_theirs'"))
        async with ctx.control_db.transaction() as session:
            await session.execute(delete(Credential).where(Credential.id == theirs.credential_id))


async def test_a_confirm_whose_rules_move_under_its_own_write_is_refused(
    integration_context: Context, binding: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rules are read again inside the transaction that replaces them.

    Whether they may be replaced is decided by a read taken before it, and
    the write is a delete and re-insert with nothing to compare against. Two
    landings would each pass their own check and the later would revert the
    earlier, with both recording success. Here the check before the write is
    made to miss the move, so only the one inside it can refuse.
    """
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))
    await CredentialService(ctx).replace_agent_permissions(
        binding, _AGENT, [_READ | {"path": "/v2/"}], identity=_OWNER
    )
    monkeypatch.setattr(connect_session_service, "baseline_matches", lambda *args, **kwargs: True)
    with pytest.raises(BaselineChangedError):
        await svc.confirm_edit(
            created.session_id,
            poll_token=None,
            identity=_OWNER,
            revision_id=created.revision_id,
            permission_rules=[_READ, _SEND],
            resume=False,
        )
    assert await _rules(ctx, binding) == [("allow", "/v2/")]


async def test_a_rebase_a_caller_asked_for_is_recorded_as_theirs(
    integration_context: Context, binding: str
) -> None:
    """A caller-invoked write leaves a trail naming who asked for it.

    The revision it appends records no author — it is nobody's ask, and the
    review renders it as the rebase it is — so the audit entry and the event
    are the only places the caller is named. A rebase with nothing to do
    writes neither.
    """
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))
    await CredentialService(ctx).replace_agent_permissions(
        binding, _AGENT, [_READ | {"path": "/v2/"}], identity=_OWNER
    )
    rebased = await svc.rebase_session(created.session_id, poll_token=None, identity=_OWNER)
    assert rebased.revision_number == 2
    # Already current: idempotent, and recorded once.
    again = await svc.rebase_session(created.session_id, poll_token=None, identity=_OWNER)
    assert (again.revision_id, again.revision_number) == (rebased.revision_id, 2)

    async with ctx.admin_db.session() as session:
        entries = [
            e
            for e in (await session.execute(select(AuditEntry))).scalars().all()
            if e.target_id == created.session_id and e.action == AuditAction.UPDATE.value
        ]
        events = [
            e
            for e in (await session.execute(select(Event))).scalars().all()
            if e.type == EventType.CONNECT_SESSION_AMENDED
            and (e.data or {}).get("session_id") == created.session_id
        ]
    assert [(e.actor_id, (e.after or {}).get("revision_id")) for e in entries] == [
        (_OWNER_SUB, rebased.revision_id)
    ]
    assert [(e.actor_id, (e.data or {})["revision_id"]) for e in events] == [
        (_OWNER_SUB, rebased.revision_id)
    ]
    async with ctx.control_db.session() as session:
        revisions = await ConnectSessionRevisionRepository.list_for_session(
            session, created.session_id
        )
    assert [r.created_by for r in revisions] == [_AGENT, None]


async def test_a_reviewer_who_does_not_own_the_credential_is_offered_no_approval(
    integration_context: Context, binding: str
) -> None:
    """Read access to the request is not authority over the secret.

    The agent's approver may read the request and reject it, but widening what
    the agent does with a credential someone else created is that creator's
    call, as the direct rule write is. The review has to say so, or it offers
    an Approve the confirm refuses.
    """
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))
    async with ctx.control_db.transaction() as session:
        await CredentialRepository.set_created_by(session, binding, created_by="usr_cse_else")

    review = await svc.get_review_data(created.session_id, poll_token=None, identity=_OWNER)
    assert review.can_confirm is False
    with pytest.raises(EditBindingNotFoundError):
        await svc.confirm_edit(
            created.session_id,
            poll_token=None,
            identity=_OWNER,
            revision_id=created.revision_id,
            permission_rules=[_READ, _SEND],
            resume=False,
        )


async def test_a_rebase_of_an_ended_request_is_the_uniform_refusal(
    integration_context: Context, binding: str
) -> None:
    """``:rebase`` writes a revision, so it is bounded as the confirm it precedes.

    Once the request has ended there is no session to bring up to date, and
    the answer is the one every other read of an ended session gives, naming
    nothing about it.
    """
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    created = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))
    await svc.confirm_edit(
        created.session_id,
        poll_token=None,
        identity=_OWNER,
        revision_id=created.revision_id,
        permission_rules=[_READ, _SEND],
        resume=False,
    )
    with pytest.raises(InvalidPollTokenError):
        await svc.rebase_session(created.session_id, poll_token=None, identity=_OWNER)


async def test_a_reach_wider_than_the_cap_says_so(
    integration_context: Context, binding: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The screen whose job is to state reach must not under-report it silently.

    Both reach queries are capped, and nothing pages past the cap, so the
    review says when the change reaches more agents than it names.
    """
    monkeypatch.setattr(connect_session_service, "_BOUND_AGENTS_LIMIT", 1)
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    async with ctx.admin_db.transaction() as session:
        for n in (1, 2):
            await session.execute(
                text(
                    "INSERT INTO agents (id, name, registered_by, owner_id, status) "
                    f"VALUES ('agnt_cse_many_{n}', 'many {n}', :u, :u, 'approved')"
                ),
                {"u": _OWNER_SUB},
            )
            await AgentCredentialBindingRepository.bind(
                session,
                agent_id=f"agnt_cse_many_{n}",
                credential_id=binding,
                created_by=_OWNER_SUB,
            )
    created = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))
    try:
        review = await svc.get_review_data(created.session_id, poll_token=None, identity=_OWNER)
        assert review.edit is not None
        assert len(review.edit.other_bound_agents) == 1
        assert review.edit.other_bound_agents_truncated is True
    finally:
        async with ctx.admin_db.transaction() as session:
            await session.execute(
                text("DELETE FROM agent_credential_bindings WHERE agent_id LIKE 'agnt_cse_many_%'")
            )
            await session.execute(text("DELETE FROM agents WHERE id LIKE 'agnt_cse_many_%'"))


async def test_a_shared_set_write_locks_the_set_the_bindings_share(
    integration_context: Context, binding: str
) -> None:
    """The lock has to cover the rows the write replaces.

    A shared set's rules are one list every binding pointing at it reads, so
    two confirms on two bindings of one set take disjoint binding locks,
    serialise on nothing, and the later one reverts the earlier with both
    recording success. Copying a curated set reads the set and writes the
    binding, so it takes both, sets first.
    """
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    admin = Identity(sub="usr_cse_admin", email="admin@test.local", permissions=["org:admin"])
    taken: list[str] = []

    async def record_set(session: Any, rule_set_id: str) -> None:
        taken.append(f"set:{rule_set_id}")

    async def record_binding(session: Any, agent_id: str, credential_id: str) -> None:
        taken.append(f"binding:{agent_id}:{credential_id}")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(PermissionRuleSetRepository, "acquire_rules_lock", record_set)
    monkeypatch.setattr(AgentPermissionRuleRepository, "acquire_binding_lock", record_binding)
    try:
        shared = await _attach_rule_set(ctx, binding, curated=False)
        created = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))
        await svc.confirm_edit(
            created.session_id,
            poll_token=None,
            identity=admin,
            revision_id=created.revision_id,
            permission_rules=[_READ, _SEND],
            resume=False,
        )
        # The set is both read and written: its lock alone serialises it.
        assert taken == [f"set:{shared}"]

        taken.clear()
        curated = await _attach_rule_set(ctx, binding, curated=True)
        copied = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND | {"path": "/v2/"})]))
        await svc.confirm_edit(
            copied.session_id,
            poll_token=None,
            identity=admin,
            revision_id=copied.revision_id,
            permission_rules=[_READ, _SEND | {"path": "/v2/"}],
            resume=False,
        )
        # Read from the set, written to the binding: both, in one order.
        assert taken == [f"set:{curated}", f"binding:{_AGENT}:{binding}"]
    finally:
        monkeypatch.undo()


async def test_a_rule_set_reach_window_filled_by_unreadable_rows_is_not_reported_as_exact(
    integration_context: Context, binding: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whether the list is complete is whether the window saturated.

    The rows past the window are unread, so whether any of them is readable
    is unknown. Counted on the survivors alone, a window filled by rows on
    credentials this reader cannot see reports "reaches no other agent"
    exactly, while readable agents sit outside it.
    """
    monkeypatch.setattr(connect_session_service, "_BOUND_AGENTS_LIMIT", 1)
    ctx = integration_context
    svc = ConnectSessionService(ctx)
    rule_set_id = await _attach_rule_set(ctx, binding, curated=False)
    strangers = []
    for n in (1, 2):
        theirs = await CredentialService(ctx).create(
            CredentialCreate(
                type=CredentialType.BEARER_TOKEN,
                name=f"cse stranger credential {n}",
                api=APIReference(vendor="acme-com", name="widgets", version="v1"),
                token=f"sk-stranger-{n}",
            ),
            identity=_OTHER,
        )
        strangers.append(theirs.credential_id)
        async with ctx.admin_db.transaction() as session:
            await session.execute(
                text(
                    "INSERT INTO agents (id, name, registered_by, owner_id, status) "
                    f"VALUES ('agnt_cse_hidden_{n}', 'hidden {n}', :u, :u, 'approved')"
                ),
                {"u": _OTHER_SUB},
            )
            await AgentCredentialBindingRepository.bind(
                session,
                agent_id=f"agnt_cse_hidden_{n}",
                credential_id=theirs.credential_id,
                created_by=_OTHER_SUB,
            )
            await PrerequisiteRepository.set_binding_rule_set(
                session,
                agent_id=f"agnt_cse_hidden_{n}",
                credential_id=theirs.credential_id,
                rule_set_id=rule_set_id,
            )
    created = await _begin(ctx, binding, EditRequest(add=[RuleAdd(_SEND)]))
    try:
        review = await svc.get_review_data(created.session_id, poll_token=None, identity=_OWNER)
        assert review.edit is not None
        # Named: none of them, since none is on a credential this reader has.
        assert review.edit.other_bound_agents == []
        # Said: the list is not the whole of the reach.
        assert review.edit.other_bound_agents_truncated is True
    finally:
        async with ctx.admin_db.transaction() as session:
            await session.execute(
                text(
                    "DELETE FROM agent_credential_bindings WHERE agent_id LIKE 'agnt_cse_hidden_%'"
                )
            )
            await session.execute(text("DELETE FROM agents WHERE id LIKE 'agnt_cse_hidden_%'"))
        async with ctx.control_db.transaction() as session:
            await session.execute(delete(Credential).where(Credential.id.in_(strangers)))
