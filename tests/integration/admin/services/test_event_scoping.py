"""Integration tests for owner scoping of event reads and acknowledgement.

An event is visible to the actor it names (``actor_id`` or ``created_by``), to
the human owner of that agent, and to ``org:admin``. System events with no
subject are visible only to ``org:admin``. Everyone else gets nothing from the
list or stream and a 404-equivalent ``EventNotFoundError`` from a direct read
or acknowledgement.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from sqlalchemy import delete, select, update

from jentic_one.admin.core.schema.agent_credential_bindings import AgentCredentialBinding
from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import AgentRepository, EventRepository, UserRepository
from jentic_one.admin.services.errors import EventNotFoundError
from jentic_one.admin.services.event_service import EventService
from jentic_one.admin.services.event_stream_service import EventStreamService
from jentic_one.admin.services.schemas.events import (
    EventAcknowledgePayload,
    EventFilter,
    EventView,
    Heartbeat,
)
from jentic_one.auth.services.agent_service import AgentService
from jentic_one.broker.core.exceptions import (
    CredentialNeedsReconnectError,
    CredentialUndecryptableError,
)
from jentic_one.broker.services.credentials.errors import RefreshInvalidGrantError
from jentic_one.broker.services.credentials.orchestrator import (
    CredentialService as BrokerCredentialService,
)
from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.core.schema.customer_api_keys import CustomerAPIKey
from jentic_one.control.core.schema.oauth_tokens import OAuthToken
from jentic_one.control.services.credentials.service import (
    CredentialService as ControlCredentialService,
)
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus, ActorType, InviteState, StoredCredentialType
from jentic_one.shared.models.events import EventType

pytestmark = pytest.mark.integration

_OTHER_SUB = "usr_evtscope_other"
_OTHER_AGENT_SUB = "agnt_evtscope_other"


@dataclass(frozen=True)
class _Seed:
    owner_id: str
    agent_id: str
    owner_event_id: str
    agent_event_id: str
    subject_event_id: str
    system_event_id: str

    @property
    def owner_visible(self) -> set[str]:
        return {self.owner_event_id, self.agent_event_id, self.subject_event_id}

    @property
    def all(self) -> set[str]:
        return {*self.owner_visible, self.system_event_id}


def _user(sub: str, *permissions: str) -> Identity:
    return Identity(sub=sub, email=f"{sub}@test.local", permissions=list(permissions))


def _agent(sub: str, *, parent: str | None) -> Identity:
    return Identity(
        sub=sub,
        permissions=["events:read", "events:write", "owner:agents:read"],
        actor_type=ActorType.AGENT,
        parent_actor_id=parent,
    )


@pytest.fixture()
async def seed(integration_context: Context) -> AsyncGenerator[_Seed, None]:
    """The owner's event, their agent's event, an event naming the owner only in
    ``created_by``, and a system event with no subject."""
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        await session.execute(delete(Event))
        owner = await UserRepository.create(
            session,
            email="evtscope-owner@test.local",
            first_name="Event",
            last_name="Owner",
            invite_state=InviteState.REDEEMED,
            created_by="usr_test",
        )
        agent = await AgentRepository.create(
            session,
            name="evtscope-agent",
            owner_id=owner.id,
            registered_by=owner.id,
            created_by=owner.id,
            status=ActorStatus.ACTIVE,
        )
        owner_event = await EventRepository.create(
            session,
            type="credential.stored",
            severity="info",
            summary="owner event",
            created_by=owner.id,
            actor_id=owner.id,
            actor_type="user",
        )
        agent_event = await EventRepository.create(
            session,
            type="execution.failed",
            severity="error",
            summary="agent event",
            requires_action=True,
            created_by=agent.id,
            actor_id=agent.id,
            actor_type="agent",
        )
        subject_event = await EventRepository.create(
            session,
            type="credential.expired",
            severity="error",
            summary="subject event",
            requires_action=True,
            created_by=owner.id,
        )
        system_event = await EventRepository.create(
            session,
            type="catalog.update_available",
            severity="info",
            summary="system event",
            requires_action=True,
            created_by=None,
        )
        await session.commit()
        seeded = _Seed(
            owner_id=owner.id,
            agent_id=agent.id,
            owner_event_id=owner_event.id,
            agent_event_id=agent_event.id,
            subject_event_id=subject_event.id,
            system_event_id=system_event.id,
        )

    yield seeded

    async with ctx.admin_db.session() as session:
        await session.execute(delete(Event))
        await session.execute(delete(Agent).where(Agent.id == seeded.agent_id))
        await session.execute(delete(User).where(User.id == seeded.owner_id))
        await session.commit()


async def _listed_ids(ctx: Context, identity: Identity, **filter_kwargs: object) -> set[str]:
    page = await EventService(ctx).list_all(
        EventFilter(**filter_kwargs),  # type: ignore[arg-type]
        identity=identity,
        limit=100,
    )
    return {e.id for e in page.data}


async def _streamed_ids(ctx: Context, identity: Identity) -> set[str]:
    """Drain one poll of the stream from a point before the seed."""
    gen = cast(
        "AsyncGenerator[EventView | Heartbeat, None]",
        EventStreamService(ctx).stream(
            identity=identity,
            since=datetime.now(UTC) - timedelta(minutes=5),
            poll_interval_seconds=0,
        ),
    )
    ids: set[str] = set()
    try:
        async for item in gen:
            if isinstance(item, Heartbeat):
                break
            ids.add(item.id)
    finally:
        await gen.aclose()
    return ids


async def test_outsiders_see_nothing_and_cannot_acknowledge(
    integration_context: Context, seed: _Seed
) -> None:
    ctx = integration_context
    outsiders = [
        _user(_OTHER_SUB, "events:read", "events:write"),
        _agent(_OTHER_AGENT_SUB, parent=_OTHER_SUB),
        # An agent never inherits its owner's (or a sibling agent's) events.
        _agent(_OTHER_AGENT_SUB, parent=seed.owner_id),
    ]
    for identity in outsiders:
        assert await _listed_ids(ctx, identity) == set()
        assert await _listed_ids(ctx, identity, actor_id=seed.agent_id) == set()
        assert await _streamed_ids(ctx, identity) == set()
        for event_id in seed.all:
            with pytest.raises(EventNotFoundError):
                await EventService(ctx).get_by_id(event_id, identity=identity)
            with pytest.raises(EventNotFoundError):
                await EventService(ctx).acknowledge(
                    event_id,
                    EventAcknowledgePayload(acknowledged=True),
                    identity=identity,
                )

    admin = _user("usr_evtscope_admin", "org:admin")
    for event_id in seed.all:
        assert (await EventService(ctx).get_by_id(event_id, identity=admin)).acknowledged is False


async def test_subject_owner_and_admin_visibility(
    integration_context: Context, seed: _Seed
) -> None:
    ctx = integration_context

    # The agent sees only its own event.
    agent = _agent(seed.agent_id, parent=seed.owner_id)
    assert await _listed_ids(ctx, agent) == {seed.agent_event_id}
    assert await _streamed_ids(ctx, agent) == {seed.agent_event_id}

    # The owner sees their own event, their agent's, and the one naming them as
    # creator — but not the system event.
    owner = _user(seed.owner_id, "events:read", "events:write")
    assert await _listed_ids(ctx, owner) == seed.owner_visible
    assert await _listed_ids(ctx, owner, requires_action=True, acknowledged=False) == {
        seed.agent_event_id,
        seed.subject_event_id,
    }
    assert await _streamed_ids(ctx, owner) == seed.owner_visible
    with pytest.raises(EventNotFoundError):
        await EventService(ctx).get_by_id(seed.system_event_id, identity=owner)
    acked = await EventService(ctx).acknowledge(
        seed.agent_event_id, EventAcknowledgePayload(acknowledged=True), identity=owner
    )
    assert acked.acknowledged is True

    # org:admin is unrestricted, system events included.
    admin = _user("usr_evtscope_admin", "org:admin")
    assert await _listed_ids(ctx, admin) == seed.all
    assert await _streamed_ids(ctx, admin) == seed.all
    acked = await EventService(ctx).acknowledge(
        seed.system_event_id, EventAcknowledgePayload(acknowledged=True), identity=admin
    )
    assert acked.acknowledged is True


async def test_stream_ignores_an_invisible_resume_point(
    integration_context: Context, seed: _Seed
) -> None:
    """A Last-Event-ID the caller cannot see does not move their resume point."""
    ctx = integration_context
    gen = cast(
        "AsyncGenerator[EventView | Heartbeat, None]",
        EventStreamService(ctx).stream(
            identity=_agent(seed.agent_id, parent=seed.owner_id),
            since=datetime.now(UTC) - timedelta(minutes=5),
            last_event_id=seed.system_event_id,
            poll_interval_seconds=0,
        ),
    )
    try:
        first = await gen.__anext__()
    finally:
        await gen.aclose()
    assert isinstance(first, EventView)
    assert first.id == seed.agent_event_id


async def test_owner_sees_events_naming_their_agent_as_creator(
    integration_context: Context, seed: _Seed
) -> None:
    """A subject-only event (e.g. expiry of an agent-created credential) reaches the
    agent and its owner through ``created_by``."""
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        event = await EventRepository.create(
            session,
            type="credential.expiring_soon",
            severity="warning",
            summary="agent credential expiring",
            created_by=seed.agent_id,
        )
        await session.commit()

    owner = _user(seed.owner_id, "events:read")
    assert event.id in await _listed_ids(ctx, owner)
    assert (await EventService(ctx).get_by_id(event.id, identity=owner)).id == event.id
    agent = _agent(seed.agent_id, parent=seed.owner_id)
    assert await _listed_ids(ctx, agent) == {seed.agent_event_id, event.id}
    assert await _listed_ids(ctx, _user(_OTHER_SUB, "events:read")) == set()


async def test_stream_resumes_after_a_visible_event(
    integration_context: Context, seed: _Seed
) -> None:
    """A non-admin resuming from an event they can see gets only the later rows
    visible to them."""
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        await session.execute(
            update(Event)
            .where(Event.id == seed.owner_event_id)
            .values(created_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await session.commit()

    gen = cast(
        "AsyncGenerator[EventView | Heartbeat, None]",
        EventStreamService(ctx).stream(
            identity=_user(seed.owner_id, "events:read"),
            last_event_id=seed.owner_event_id,
            poll_interval_seconds=0,
            overlap_seconds=0,
        ),
    )
    ids: set[str] = set()
    try:
        async for item in gen:
            if isinstance(item, Heartbeat):
                break
            ids.add(item.id)
    finally:
        await gen.aclose()
    assert ids == {seed.agent_event_id, seed.subject_event_id}


_CRED_VENDOR = "evtscope.example"
_CRED_OK = "cred_evtscope_ok"
_CRED_BAD = "cred_evtscope_bad"
_CRED_OAUTH = "cred_evtscope_oauth"
_ADMIN = Identity(
    sub="usr_evtscope_admin", email="usr_evtscope_admin@test.local", permissions=["org:admin"]
)


@dataclass(frozen=True)
class _CrossOwner:
    """A credential owned by one user, used by an agent owned by another."""

    credential_owner_id: str
    agent_owner_id: str
    agent_id: str

    @property
    def agent(self) -> Identity:
        return Identity(
            sub=self.agent_id,
            actor_type=ActorType.AGENT,
            permissions=["execute"],
            parent_actor_id=self.agent_owner_id,
        )


@pytest.fixture()
async def cross_owner(integration_context: Context) -> AsyncGenerator[_CrossOwner, None]:
    """User A owns three credentials (a working API key, an API key whose
    ciphertext does not decrypt, and an OAuth token); user B owns the agent."""
    ctx = integration_context
    cred_ids = [_CRED_OK, _CRED_BAD, _CRED_OAUTH]

    async def _clean_control() -> None:
        async with ctx.control_db.session() as session:
            await session.execute(
                delete(AgentPermissionRule).where(AgentPermissionRule.credential_id.in_(cred_ids))
            )
            await session.execute(delete(OAuthToken).where(OAuthToken.credential_id.in_(cred_ids)))
            await session.execute(
                delete(CustomerAPIKey).where(CustomerAPIKey.credential_id.in_(cred_ids))
            )
            await session.execute(delete(Credential).where(Credential.id.in_(cred_ids)))
            await session.commit()

    await _clean_control()
    async with ctx.admin_db.session() as session:
        await session.execute(delete(Event))
        users = []
        for role in ("credowner", "agentowner"):
            users.append(
                await UserRepository.create(
                    session,
                    email=f"evtscope-{role}@test.local",
                    first_name="Event",
                    last_name=role,
                    invite_state=InviteState.REDEEMED,
                    created_by="usr_test",
                )
            )
        credential_owner, agent_owner = users
        agent = await AgentRepository.create(
            session,
            name="evtscope-cross-agent",
            owner_id=agent_owner.id,
            registered_by=agent_owner.id,
            created_by=agent_owner.id,
            status=ActorStatus.ACTIVE,
        )
        await session.commit()
        seeded = _CrossOwner(
            credential_owner_id=credential_owner.id,
            agent_owner_id=agent_owner.id,
            agent_id=agent.id,
        )

    async with ctx.control_db.session() as session:
        for cred_id, stored_type in (
            (_CRED_OK, StoredCredentialType.API_KEY),
            (_CRED_BAD, StoredCredentialType.API_KEY),
            (_CRED_OAUTH, StoredCredentialType.OAUTH2_AUTHORIZATION_CODE),
        ):
            session.add(
                Credential(
                    id=cred_id,
                    type=stored_type,
                    name=f"cred-{cred_id}",
                    api_vendor=_CRED_VENDOR,
                    provider="static",
                    created_by=seeded.credential_owner_id,
                )
            )
        await session.flush()
        session.add(
            CustomerAPIKey(
                id=f"key-{_CRED_OK}",
                credential_id=_CRED_OK,
                encrypted_key=ctx.encryption.encrypt("sk-evtscope"),
                location="header",
                field_name="X-Api-Key",
            )
        )
        session.add(
            CustomerAPIKey(
                id=f"key-{_CRED_BAD}",
                credential_id=_CRED_BAD,
                encrypted_key="not-a-ciphertext",
                location="header",
                field_name="X-Api-Key",
            )
        )
        session.add(
            OAuthToken(
                id=f"oat-{_CRED_OAUTH}",
                credential_id=_CRED_OAUTH,
                encrypted_access_token=ctx.encryption.encrypt("access"),
                encrypted_refresh_token=ctx.encryption.encrypt("refresh"),
                expires_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        await session.commit()

    yield seeded

    await _clean_control()
    async with ctx.admin_db.session() as session:
        await session.execute(delete(Event))
        await session.execute(
            delete(AgentCredentialBinding).where(AgentCredentialBinding.agent_id == seeded.agent_id)
        )
        await session.execute(delete(Agent).where(Agent.id == seeded.agent_id))
        await session.execute(
            delete(User).where(User.id.in_([seeded.credential_owner_id, seeded.agent_owner_id]))
        )
        await session.commit()


async def _events_by_type(ctx: Context, *types: str) -> dict[str, list[Event]]:
    async with ctx.admin_db.session() as session:
        rows = (await session.execute(select(Event).where(Event.type.in_(types)))).scalars()
        grouped: dict[str, list[Event]] = {t: [] for t in types}
        for row in rows:
            grouped[row.type].append(row)
    return grouped


async def test_credential_use_events_reach_the_credential_owner(
    integration_context: Context, cross_owner: _CrossOwner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use and health events for A's credential, raised by B's agent, name A in
    ``created_by`` and the agent in ``actor_id``: A and B see them, C does not."""
    ctx = integration_context
    broker = BrokerCredentialService(ctx)
    agent = cross_owner.agent

    await broker.inject(
        api_vendor=_CRED_VENDOR,
        api_name="",
        api_version="",
        identity=agent,
        credential_id=_CRED_OK,
    )
    with pytest.raises(CredentialUndecryptableError):
        await broker.inject(
            api_vendor=_CRED_VENDOR,
            api_name="",
            api_version="",
            identity=agent,
            credential_id=_CRED_BAD,
        )

    # The token endpoint is the out-of-process boundary: it rejects the refresh.
    class _RejectingRefresher:
        def __init__(self, _ctx: Context) -> None:
            pass

        async def ensure_fresh(self, **_kwargs: object) -> str:
            raise RefreshInvalidGrantError(_CRED_OAUTH)

    monkeypatch.setattr(
        "jentic_one.broker.services.credentials.orchestrator.TokenRefresher", _RejectingRefresher
    )
    with pytest.raises(CredentialNeedsReconnectError):
        await broker.inject(
            api_vendor=_CRED_VENDOR,
            api_name="",
            api_version="",
            identity=agent,
            credential_id=_CRED_OAUTH,
        )

    types = (
        EventType.CREDENTIAL_ACCESSED,
        EventType.CREDENTIAL_UNDECRYPTABLE,
        EventType.CREDENTIAL_REFRESH_FAILED,
    )
    grouped = await _events_by_type(ctx, *types)
    event_ids: set[str] = set()
    for event_type in types:
        [event] = grouped[event_type]
        assert event.actor_id == cross_owner.agent_id
        assert event.created_by == cross_owner.credential_owner_id
        event_ids.add(event.id)
    # Summaries name the stored credential; its id stays in ``data``.
    [accessed] = grouped[EventType.CREDENTIAL_ACCESSED]
    assert accessed.summary == (
        f"Credential 'cred-{_CRED_OK}' accessed by {cross_owner.agent_id} for {_CRED_VENDOR}"
    )
    assert accessed.data["credential_id"] == _CRED_OK
    [undecryptable] = grouped[EventType.CREDENTIAL_UNDECRYPTABLE]
    assert undecryptable.summary == (
        f"Credential 'cred-{_CRED_BAD}' cannot be decrypted for '{_CRED_VENDOR}'"
    )
    assert undecryptable.data == {"credential_id": _CRED_BAD, "api_vendor": _CRED_VENDOR}

    credential_owner = _user(cross_owner.credential_owner_id, "events:read")
    agent_owner = _user(cross_owner.agent_owner_id, "events:read")
    outsider = _user(_OTHER_SUB, "events:read")
    assert await _listed_ids(ctx, credential_owner) == event_ids
    assert await _listed_ids(ctx, agent_owner) == event_ids
    assert await _listed_ids(ctx, outsider) == set()
    assert await _streamed_ids(ctx, credential_owner) == event_ids
    for event_id in event_ids:
        assert (await EventService(ctx).get_by_id(event_id, identity=credential_owner)).id == (
            event_id
        )
        with pytest.raises(EventNotFoundError):
            await EventService(ctx).get_by_id(event_id, identity=outsider)


async def test_binding_events_reach_the_agent_owner(
    integration_context: Context, cross_owner: _CrossOwner
) -> None:
    """Binding lifecycle and per-binding rule events name the agent in
    ``created_by`` and the acting caller in ``actor_id``, so the agent's owner sees
    changes made by someone else."""
    ctx = integration_context
    agents = AgentService(ctx)
    await agents.bind_credential(cross_owner.agent_id, credential_id=_CRED_OK, identity=_ADMIN)
    credential_owner = _user(cross_owner.credential_owner_id, "credentials:write")
    await ControlCredentialService(ctx).replace_agent_permissions(
        _CRED_OK,
        cross_owner.agent_id,
        [{"effect": "allow", "methods": ["GET"], "path": ".*"}],
        identity=credential_owner,
    )
    await agents.unbind_credential(
        cross_owner.agent_id, credential_id=_CRED_OK, purge=False, identity=_ADMIN
    )
    await agents.resume_credential(cross_owner.agent_id, credential_id=_CRED_OK, identity=_ADMIN)

    grouped = await _events_by_type(
        ctx,
        EventType.CREDENTIAL_BOUND_TO_AGENT,
        EventType.CREDENTIAL_UNBOUND_FROM_AGENT,
        EventType.CREDENTIAL_PERMISSION_RULE_SET,
    )
    bound = grouped[EventType.CREDENTIAL_BOUND_TO_AGENT]
    unbound = grouped[EventType.CREDENTIAL_UNBOUND_FROM_AGENT]
    [rules] = grouped[EventType.CREDENTIAL_PERMISSION_RULE_SET]
    assert len(bound) == 2
    assert len(unbound) == 1
    for event in (*bound, *unbound):
        assert event.actor_id == _ADMIN.sub
        assert event.created_by == cross_owner.agent_id
    assert rules.actor_id == cross_owner.credential_owner_id
    assert rules.created_by == cross_owner.agent_id

    # Summaries name the credential and the agent; both ids stay in ``data``.
    ids = {"agent_id": cross_owner.agent_id, "credential_id": _CRED_OK}
    credential, agent_name = f"'cred-{_CRED_OK}'", "'evtscope-cross-agent'"
    assert sorted(e.summary for e in bound) == [
        f"Credential {credential} binding resumed for agent {agent_name}",
        f"Credential {credential} bound to agent {agent_name}",
    ]
    assert unbound[0].summary == f"Credential {credential} suspended for agent {agent_name}"
    assert rules.summary == (
        f"Permission rules set on agent {agent_name} for credential {credential}"
    )
    for event in (*bound, *unbound, rules):
        assert event.data == ids

    every_id = {e.id for e in (*bound, *unbound, rules)}
    assert await _listed_ids(ctx, _user(cross_owner.agent_owner_id, "events:read")) == every_id
    assert await _listed_ids(ctx, cross_owner.agent) == every_id
    # The credential owner sees only the change they made.
    assert await _listed_ids(ctx, _user(cross_owner.credential_owner_id, "events:read")) == {
        rules.id
    }
    assert await _listed_ids(ctx, _user(_OTHER_SUB, "events:read")) == set()
