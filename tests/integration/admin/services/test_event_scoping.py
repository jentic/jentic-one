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
from sqlalchemy import delete, update

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
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus, ActorType, InviteState

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
