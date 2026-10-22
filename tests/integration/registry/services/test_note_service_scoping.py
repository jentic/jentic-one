"""Integration tests for caller visibility on note reads and mutations.

A note is visible to its creator and to ``org:admin``; a user also reads the
notes of agents they own. Updates and deletes stay with the note's author (and
``org:admin``): a note the caller cannot write is reported as not found, before
any ``If-Match`` revision check, and the row is left unchanged.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, select

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.users import User
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.notes import Note
from jentic_one.registry.services.errors import NoteNotFoundError
from jentic_one.registry.services.note_service import NoteService, NoteView
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ActorType

pytestmark = pytest.mark.integration

_OWNER = Identity(sub="usr_note_owner", email="owner@example.com")
_OTHER = Identity(sub="usr_note_other", email="other@example.com")
_ADMIN = Identity(sub="usr_note_admin", email="admin@example.com", permissions=["org:admin"])


async def _create_note(ctx: Context, api: Api) -> NoteView:
    return await NoteService(ctx).create(
        resource_api=(api.vendor, api.name, api.version),
        body="Owner body",
        identity=_OWNER,
    )


async def _load(registry_db: DatabaseSession, note_id: str) -> Note | None:
    async with registry_db.session() as session:
        result = await session.execute(select(Note).where(Note.id == note_id))
        return result.scalar_one_or_none()


async def test_get_by_other_user_not_found(integration_context: Context, note_api: Api) -> None:
    view = await _create_note(integration_context, note_api)

    with pytest.raises(NoteNotFoundError):
        await NoteService(integration_context).get(view.id, identity=_OTHER)


@pytest.mark.parametrize("if_match", [None, 1, 99])
async def test_update_by_other_user_not_found_and_row_unchanged(
    integration_context: Context,
    registry_db: DatabaseSession,
    note_api: Api,
    if_match: int | None,
) -> None:
    # A matching (1) or stale (99) If-Match must not reveal the note: the
    # visibility check runs first, so the result is not-found, not a 412.
    view = await _create_note(integration_context, note_api)

    with pytest.raises(NoteNotFoundError):
        await NoteService(integration_context).update(
            view.id, if_match=if_match, body="Overwritten", identity=_OTHER
        )

    row = await _load(registry_db, view.id)
    assert row is not None
    assert row.body == "Owner body"
    assert row.revision == view.revision


@pytest.mark.parametrize("if_match", [None, 1, 99])
async def test_delete_by_other_user_not_found_and_row_kept(
    integration_context: Context,
    registry_db: DatabaseSession,
    note_api: Api,
    if_match: int | None,
) -> None:
    view = await _create_note(integration_context, note_api)

    with pytest.raises(NoteNotFoundError):
        await NoteService(integration_context).delete(view.id, if_match=if_match, identity=_OTHER)

    row = await _load(registry_db, view.id)
    assert row is not None
    assert row.revision == view.revision


async def test_owner_can_update_and_delete(
    integration_context: Context, registry_db: DatabaseSession, note_api: Api
) -> None:
    svc = NoteService(integration_context)
    view = await _create_note(integration_context, note_api)

    updated = await svc.update(view.id, if_match=view.revision, body="Owner edit", identity=_OWNER)
    assert updated.body == "Owner edit"
    assert updated.revision == view.revision + 1

    await svc.delete(view.id, if_match=updated.revision, identity=_OWNER)
    assert await _load(registry_db, view.id) is None


async def test_admin_can_read_update_and_delete_any_note(
    integration_context: Context, registry_db: DatabaseSession, note_api: Api
) -> None:
    svc = NoteService(integration_context)
    view = await _create_note(integration_context, note_api)

    assert (await svc.get(view.id, identity=_ADMIN)).id == view.id

    updated = await svc.update(view.id, body="Admin edit", identity=_ADMIN)
    assert updated.body == "Admin edit"
    assert updated.created_by == _OWNER.sub

    await svc.delete(view.id, identity=_ADMIN)
    assert await _load(registry_db, view.id) is None


# --- owned-agent read widening (#1857) ---------------------------------------
#
# A user also reads the notes their own agents wrote; writes stay with the
# note's author. An agent sees only its own notes: owner delegation does not
# widen notes.

_OWNER_AGENT = "agt_note_owner_a"
_OTHER_AGENT = "agt_note_other_a"
_AGENT_OWNERS = {_OWNER_AGENT: _OWNER.sub, _OTHER_AGENT: _OTHER.sub}
_CREDENTIAL_ID = "cred_note_owned_agent"


def _agent(agent_id: str, *, delegated: bool = False) -> Identity:
    return Identity(
        sub=agent_id,
        actor_type=ActorType.AGENT,
        parent_actor_id=_AGENT_OWNERS[agent_id],
        permissions=["owner:resources:read", "owner:agents:read"] if delegated else [],
    )


async def _wipe_agents(admin_db: DatabaseSession) -> None:
    async with admin_db.session() as session:
        await session.execute(delete(Agent).where(Agent.id.in_(list(_AGENT_OWNERS))))
        await session.execute(delete(User).where(User.id.in_([_OWNER.sub, _OTHER.sub])))
        await session.commit()


@pytest.fixture()
async def owned_agents(admin_db: DatabaseSession) -> AsyncGenerator[None, None]:
    """``_OWNER`` owns ``_OWNER_AGENT``; ``_OTHER`` owns ``_OTHER_AGENT``."""
    await _wipe_agents(admin_db)
    async with admin_db.transaction() as session:
        for user in (_OWNER, _OTHER):
            session.add(
                User(id=user.sub, email=f"{user.sub}@test.local", first_name="T", last_name="U")
            )
        await session.flush()
        for agent_id, owner_id in _AGENT_OWNERS.items():
            session.add(
                Agent(id=agent_id, name=agent_id, owner_id=owner_id, registered_by=owner_id)
            )
    yield
    await _wipe_agents(admin_db)


async def _agent_note(ctx: Context, agent_id: str, body: str) -> NoteView:
    return await NoteService(ctx).create(
        resource_credential_id=_CREDENTIAL_ID, body=body, identity=_agent(agent_id)
    )


async def _listed(
    ctx: Context,
    identity: Identity,
    *,
    created_by: str | None = None,
    credential_id: str | None = None,
) -> set[str]:
    page = await NoteService(ctx).list_page(
        identity=identity, created_by=created_by, credential_id=credential_id
    )
    assert page.has_more is False
    return {view.id for view in page.data}


async def test_owner_reads_own_agents_notes(
    integration_context: Context, note_api: Api, owned_agents: None
) -> None:
    own = await _create_note(integration_context, note_api)
    mine = await _agent_note(integration_context, _OWNER_AGENT, "owner agent")
    theirs = await _agent_note(integration_context, _OTHER_AGENT, "other agent")
    svc = NoteService(integration_context)

    assert await _listed(integration_context, _OWNER) == {own.id, mine.id}
    assert (await svc.get(mine.id, identity=_OWNER)).created_by == _OWNER_AGENT
    with pytest.raises(NoteNotFoundError):
        await svc.get(theirs.id, identity=_OWNER)

    assert await _listed(integration_context, _OTHER) == {theirs.id}
    with pytest.raises(NoteNotFoundError):
        await svc.get(mine.id, identity=_OTHER)


@pytest.mark.parametrize(
    ("created_by", "credential_id"),
    [(_OWNER_AGENT, None), (None, _CREDENTIAL_ID)],
    ids=["created_by", "credential_id"],
)
async def test_owner_query_filters_include_own_agents_notes(
    integration_context: Context,
    note_api: Api,
    owned_agents: None,
    created_by: str | None,
    credential_id: str | None,
) -> None:
    mine = await _agent_note(integration_context, _OWNER_AGENT, "owner agent")
    theirs = await _agent_note(integration_context, _OTHER_AGENT, "other agent")

    owner_view = await _listed(
        integration_context, _OWNER, created_by=created_by, credential_id=credential_id
    )
    assert owner_view == {mine.id}
    other_view = await _listed(
        integration_context, _OTHER, created_by=created_by, credential_id=credential_id
    )
    assert mine.id not in other_view
    if credential_id is not None:
        assert other_view == {theirs.id}


async def test_other_user_filtering_by_foreign_agent_sees_nothing(
    integration_context: Context, note_api: Api, owned_agents: None
) -> None:
    await _agent_note(integration_context, _OWNER_AGENT, "owner agent")

    assert await _listed(integration_context, _OTHER, created_by=_OWNER_AGENT) == set()


@pytest.mark.parametrize("if_match", [None, 1, 99])
async def test_owner_cannot_update_or_delete_agents_note(
    integration_context: Context,
    registry_db: DatabaseSession,
    note_api: Api,
    owned_agents: None,
    if_match: int | None,
) -> None:
    view = await _agent_note(integration_context, _OWNER_AGENT, "Agent body")
    svc = NoteService(integration_context)

    with pytest.raises(NoteNotFoundError):
        await svc.update(view.id, if_match=if_match, body="Overwritten", identity=_OWNER)
    with pytest.raises(NoteNotFoundError):
        await svc.delete(view.id, if_match=if_match, identity=_OWNER)

    row = await _load(registry_db, view.id)
    assert row is not None
    assert row.body == "Agent body"
    assert row.revision == view.revision


async def test_agent_can_update_and_delete_own_note(
    integration_context: Context,
    registry_db: DatabaseSession,
    note_api: Api,
    owned_agents: None,
) -> None:
    view = await _agent_note(integration_context, _OWNER_AGENT, "Agent body")
    svc = NoteService(integration_context)
    agent = _agent(_OWNER_AGENT)

    updated = await svc.update(view.id, if_match=view.revision, body="Agent edit", identity=agent)
    assert updated.body == "Agent edit"

    await svc.delete(view.id, if_match=updated.revision, identity=agent)
    assert await _load(registry_db, view.id) is None


@pytest.mark.parametrize("delegated", [False, True], ids=["plain", "owner-delegated"])
async def test_agent_sees_only_its_own_notes(
    integration_context: Context, note_api: Api, owned_agents: None, delegated: bool
) -> None:
    # Owner delegation (owner:*:read + parent_actor_id) widens credentials and
    # agents, never notes: the agent sees neither its owner's notes nor a
    # sibling agent's.
    owner_note = await _create_note(integration_context, note_api)
    own = await _agent_note(integration_context, _OWNER_AGENT, "own")
    await _agent_note(integration_context, _OTHER_AGENT, "other")
    agent = _agent(_OWNER_AGENT, delegated=delegated)

    assert await _listed(integration_context, agent) == {own.id}
    with pytest.raises(NoteNotFoundError):
        await NoteService(integration_context).get(owner_note.id, identity=agent)
