"""Integration tests for resolving actors by id (``ActorService.lookup``).

Agents resolve only when the caller may see them (the admin agent scoping
filter). Users resolve only when the caller can already see them by id: itself,
the owner or approver of an agent it can see, the actor on an event it can see,
and the creator of a credential it can read (shared credentials included). Any
other id is left out exactly like an unknown id. ``org:admin`` resolves every
actor.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import delete, update
from sqlalchemy.sql.elements import ColumnElement

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.events import Event
from jentic_one.admin.core.schema.users import User
from jentic_one.admin.repos import AgentRepository, EventRepository, UserRepository
from jentic_one.admin.services.actor_service import ActorService
from jentic_one.control.core.schema.credentials import Credential
from jentic_one.control.scoping import filters as control_filters
from jentic_one.control.services.credentials.service import CredentialService
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import OWNER_AGENTS_READ
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorStatus, ActorType, InviteState

pytestmark = pytest.mark.integration

_ADMIN = Identity(sub="usr_lookup_admin", email="a@test.local", permissions=["org:admin"])
_OUTSIDER = Identity(sub="usr_lookup_outsider", email="o@test.local", permissions=["agents:read"])
_UNKNOWN_USER = "usr_does_not_exist"


def _owner(user_id: str, *permissions: str) -> Identity:
    return Identity(sub=user_id, email="ada@test.local", permissions=["agents:read", *permissions])


def _agent(agent_id: str, owner_id: str, *permissions: str) -> Identity:
    return Identity(
        sub=agent_id,
        permissions=list(permissions),
        actor_type=ActorType.AGENT,
        parent_actor_id=owner_id,
    )


@dataclass(frozen=True)
class _Seed:
    user_id: str
    approver_id: str
    creator_id: str
    event_actor_id: str
    stranger_id: str
    active_agent_id: str
    pending_agent_id: str
    credential_id: str
    event_id: str

    @property
    def user_ids(self) -> list[str]:
        return [
            self.user_id,
            self.approver_id,
            self.creator_id,
            self.event_actor_id,
            self.stranger_id,
        ]


@pytest.fixture()
async def seed(integration_context: Context) -> AsyncGenerator[_Seed, None]:
    """Ada owns an active (approved) and a pending agent, plus users related to her or not.

    - the approver approved Ada's active agent;
    - the creator created a credential Ada cannot read unless it is shared;
    - the event actor acted on an event about Ada;
    - the stranger has no relation to Ada at all.
    """
    ctx = integration_context
    async with ctx.admin_db.session() as session:
        users = {}
        for key, first in (
            ("user", "Ada"),
            ("approver", "Approver"),
            ("creator", "Creator"),
            ("event_actor", "Eve"),
            ("stranger", "Stranger"),
        ):
            users[key] = await UserRepository.create(
                session,
                email=f"actor-lookup-{key}@test.local",
                first_name=first,
                last_name="Lovelace" if key == "user" else "User",
                invite_state=InviteState.REDEEMED,
                created_by="usr_test",
            )
        user_id = users["user"].id
        active = await AgentRepository.create(
            session,
            name="lookup-active",
            owner_id=user_id,
            registered_by=user_id,
            created_by=user_id,
            status=ActorStatus.ACTIVE,
        )
        pending = await AgentRepository.create(
            session,
            name="lookup-pending",
            owner_id=user_id,
            registered_by=user_id,
            created_by=user_id,
            status=ActorStatus.PENDING,
        )
        await session.execute(
            update(Agent).where(Agent.id == active.id).values(approved_by=users["approver"].id)
        )
        event = await EventRepository.create(
            session,
            type="credential.used",
            severity="info",
            summary="lookup seed event",
            created_by=user_id,
            actor_id=users["event_actor"].id,
            actor_type="user",
        )
        await session.commit()
    async with ctx.control_db.session() as session:
        credential = Credential(
            type="token_value",
            name="lookup-creator-credential",
            api_vendor="lookup.example.com",
            created_by=users["creator"].id,
        )
        session.add(credential)
        await session.commit()
        credential_id = credential.id

    seeded = _Seed(
        user_id=user_id,
        approver_id=users["approver"].id,
        creator_id=users["creator"].id,
        event_actor_id=users["event_actor"].id,
        stranger_id=users["stranger"].id,
        active_agent_id=active.id,
        pending_agent_id=pending.id,
        credential_id=credential_id,
        event_id=event.id,
    )

    yield seeded

    async with ctx.control_db.session() as session:
        await session.execute(delete(Credential).where(Credential.id == seeded.credential_id))
        await session.commit()
    async with ctx.admin_db.session() as session:
        await session.execute(delete(Event).where(Event.id == seeded.event_id))
        await session.execute(
            delete(Agent).where(Agent.id.in_([seeded.active_agent_id, seeded.pending_agent_id]))
        )
        await session.execute(delete(User).where(User.id.in_(seeded.user_ids)))
        await session.commit()


@pytest.fixture()
def share_credential_with_owner(seed: _Seed) -> Iterator[None]:
    """Register a shared-read provider that lets Ada read the creator's credential."""

    def _provider(identity: Identity, model: type) -> ColumnElement[bool] | None:
        if model is Credential and identity.sub == seed.user_id:
            return Credential.id == seed.credential_id
        return None

    control_filters.register_access_filter_provider(_provider)
    yield
    control_filters._ACCESS_FILTER_PROVIDERS.remove(_provider)


def _service(ctx: Context) -> ActorService:
    return ActorService(ctx, credential_creators=CredentialService(ctx))


async def test_admin_resolves_every_actor(integration_context: Context, seed: _Seed) -> None:
    views = await _service(integration_context).lookup(
        [*seed.user_ids, seed.active_agent_id, seed.pending_agent_id], identity=_ADMIN
    )
    by_id = {v.id: v for v in views}
    assert set(by_id) == {*seed.user_ids, seed.active_agent_id, seed.pending_agent_id}
    assert by_id[seed.user_id].actor_type == ActorType.USER
    assert by_id[seed.user_id].name == "Ada Lovelace"
    assert by_id[seed.user_id].active is True
    assert by_id[seed.active_agent_id].actor_type == ActorType.AGENT
    assert by_id[seed.active_agent_id].name == "lookup-active"
    assert by_id[seed.active_agent_id].active is True
    assert by_id[seed.pending_agent_id].active is False


async def test_owner_resolves_self_agents_approver_and_event_actor(
    integration_context: Context, seed: _Seed
) -> None:
    views = await _service(integration_context).lookup(
        [
            seed.user_id,
            seed.active_agent_id,
            seed.pending_agent_id,
            seed.approver_id,
            seed.event_actor_id,
        ],
        identity=_owner(seed.user_id),
    )
    assert {v.id for v in views} == {
        seed.user_id,
        seed.active_agent_id,
        seed.pending_agent_id,
        seed.approver_id,
        seed.event_actor_id,
    }


@pytest.mark.parametrize("permissions", [(), ("users:read",)], ids=["member", "users-read"])
async def test_unrelated_user_is_left_out_like_an_unknown_id(
    integration_context: Context, seed: _Seed, permissions: tuple[str, ...]
) -> None:
    """A user the caller has no relation to answers exactly like an id that does not exist.

    ``users:read`` does not widen this: the ``User`` read filter it shares
    with ``GET /users/{id}`` is self-only for a non-admin.
    """
    svc = _service(integration_context)
    identity = _owner(seed.user_id, *permissions)
    unrelated = await svc.lookup([seed.stranger_id, seed.creator_id], identity=identity)
    unknown = await svc.lookup([_UNKNOWN_USER], identity=identity)
    assert unrelated == unknown == []


async def test_outsider_resolves_nobody_related_to_someone_else(
    integration_context: Context, seed: _Seed
) -> None:
    views = await _service(integration_context).lookup(
        [*seed.user_ids, seed.active_agent_id, seed.pending_agent_id], identity=_OUTSIDER
    )
    assert views == []


async def test_owner_resolves_creator_of_a_shared_credential(
    integration_context: Context, seed: _Seed, share_credential_with_owner: None
) -> None:
    """Credential visibility widened through the access-filter provider seam names its creator."""
    views = await _service(integration_context).lookup(
        [seed.creator_id, seed.stranger_id], identity=_owner(seed.user_id)
    )
    assert [(v.id, v.name) for v in views] == [(seed.creator_id, "Creator User")]


async def test_creator_is_left_out_without_the_credential_lookup(
    integration_context: Context, seed: _Seed, share_credential_with_owner: None
) -> None:
    """An admin process that cannot read credentials labels no user through them."""
    views = await ActorService(integration_context).lookup(
        [seed.creator_id], identity=_owner(seed.user_id)
    )
    assert views == []


async def test_agent_resolves_itself_its_owner_and_its_approver(
    integration_context: Context, seed: _Seed
) -> None:
    identity = _agent(seed.active_agent_id, seed.user_id, "agents:read")
    views = await _service(integration_context).lookup(
        [
            seed.user_id,
            seed.approver_id,
            seed.active_agent_id,
            seed.pending_agent_id,
            seed.stranger_id,
        ],
        identity=identity,
    )
    assert {v.id for v in views} == {seed.user_id, seed.approver_id, seed.active_agent_id}


async def test_agent_with_owner_scope_resolves_owners_agents(
    integration_context: Context, seed: _Seed
) -> None:
    identity = _agent(seed.active_agent_id, seed.user_id, "agents:read", OWNER_AGENTS_READ)
    views = await _service(integration_context).lookup(
        [seed.pending_agent_id, seed.approver_id], identity=identity
    )
    assert {v.id for v in views} == {seed.pending_agent_id, seed.approver_id}


async def test_lookup_omits_unknown_and_collapses_duplicates(
    integration_context: Context, seed: _Seed
) -> None:
    views = await _service(integration_context).lookup(
        [seed.user_id, _UNKNOWN_USER, seed.user_id, "cred_not_an_actor"], identity=_ADMIN
    )
    assert [v.id for v in views] == [seed.user_id]


async def test_lookup_of_no_ids_is_empty(integration_context: Context) -> None:
    assert await _service(integration_context).lookup([], identity=_ADMIN) == []
