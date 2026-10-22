"""Web tests for note visibility on the ``/notes`` routes.

A note is visible to its creator and to ``org:admin``; a user also reads the
notes of agents they own. Updates and deletes stay with the note's author (and
``org:admin``) — a caller who cannot write the note gets a 404 problem
document, including when the request carries an ``If-Match`` revision, and the
note is left unchanged.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import delete

from jentic_one.admin.core.schema.agents import Agent
from jentic_one.admin.core.schema.users import User
from jentic_one.registry.core.schema.notes import Note
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from tests.web.registry.conftest import _build_app_as

pytestmark = pytest.mark.integration

_OWNER = Identity(sub="usr_web_note_owner", email="note-owner@test.local")
_OWNER_AGENT = Identity(
    sub="agt_web_note_owner_a",
    actor_type=ActorType.AGENT,
    parent_actor_id=_OWNER.sub,
)
_OTHER = Identity(sub="usr_web_note_other", email="note-other@test.local")
_ADMIN = Identity(
    sub="usr_web_note_admin", email="note-admin@test.local", permissions=["org:admin"]
)


@pytest.fixture()
async def _clean_notes(web_context: Context) -> AsyncGenerator[None, None]:
    subs = [_OWNER.sub, _OWNER_AGENT.sub, _OTHER.sub, _ADMIN.sub]

    async def _wipe() -> None:
        async with web_context.registry_db.session() as session:
            await session.execute(delete(Note).where(Note.created_by.in_(subs)))
            await session.commit()
        async with web_context.admin_db.session() as session:
            await session.execute(delete(Agent).where(Agent.id == _OWNER_AGENT.sub))
            await session.execute(delete(User).where(User.id == _OWNER.sub))
            await session.commit()

    await _wipe()
    async with web_context.admin_db.transaction() as session:
        session.add(User(id=_OWNER.sub, email=_OWNER.email, first_name="T", last_name="U"))
        await session.flush()
        session.add(
            Agent(
                id=_OWNER_AGENT.sub,
                name=_OWNER_AGENT.sub,
                owner_id=_OWNER.sub,
                registered_by=_OWNER.sub,
            )
        )
    yield
    await _wipe()


def _client_as(ctx: Context, identity: Identity) -> TestClient:
    app = _build_app_as(ctx, identity)
    return TestClient(app, headers={"Authorization": "Bearer test-token"})


@pytest.fixture()
def clients(web_context: Context, _clean_notes: None) -> Iterator[dict[str, TestClient]]:
    with (
        _client_as(web_context, _OWNER) as owner,
        _client_as(web_context, _OWNER_AGENT) as owner_agent,
        _client_as(web_context, _OTHER) as other,
        _client_as(web_context, _ADMIN) as admin,
    ):
        yield {"owner": owner, "owner_agent": owner_agent, "other": other, "admin": admin}


def _create_note(client: TestClient) -> dict[str, Any]:
    resp = client.post(
        "/notes",
        json={"resource": {"execution_id": "exec_web_note"}, "body": "Owner body"},
    )
    assert resp.status_code == 201, resp.text
    note: dict[str, Any] = resp.json()
    return note


def _assert_note_not_found(resp: Response) -> None:
    assert resp.status_code == 404
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["type"] == "note_not_found"


@pytest.mark.parametrize("if_match", [None, '"1"', '"99"'])
def test_other_user_cannot_read_update_or_delete_note(
    clients: dict[str, TestClient], if_match: str | None
) -> None:
    note = _create_note(clients["owner"])
    path = f"/notes/{note['note_id']}"
    headers = {"If-Match": if_match} if if_match is not None else {}

    other = clients["other"]
    _assert_note_not_found(other.get(path))
    _assert_note_not_found(other.patch(path, json={"body": "Overwritten"}, headers=headers))
    _assert_note_not_found(other.delete(path, headers=headers))

    owner_view = clients["owner"].get(path)
    assert owner_view.status_code == 200
    assert owner_view.json()["body"] == "Owner body"
    assert owner_view.json()["revision"] == note["revision"]


def test_owner_stale_if_match_returns_412(clients: dict[str, TestClient]) -> None:
    note = _create_note(clients["owner"])
    path = f"/notes/{note['note_id']}"

    resp = clients["owner"].patch(path, json={"body": "Edit"}, headers={"If-Match": '"99"'})
    assert resp.status_code == 412


def test_admin_can_update_and_delete_any_note(clients: dict[str, TestClient]) -> None:
    note = _create_note(clients["owner"])
    path = f"/notes/{note['note_id']}"
    admin = clients["admin"]

    resp = admin.patch(path, json={"body": "Admin edit"}, headers={"If-Match": '"1"'})
    assert resp.status_code == 200, resp.text
    assert resp.json()["body"] == "Admin edit"

    assert admin.delete(path).status_code == 204
    _assert_note_not_found(clients["owner"].get(path))


def test_notes_unauthenticated_returns_401(unauthed_client: TestClient) -> None:
    assert unauthed_client.patch("/notes/note_missing", json={"body": "x"}).status_code == 401
    assert unauthed_client.delete("/notes/note_missing").status_code == 401


@pytest.mark.parametrize("if_match", [None, '"1"', '"99"'])
def test_owner_reads_but_cannot_mutate_owned_agents_note(
    clients: dict[str, TestClient], if_match: str | None
) -> None:
    note = _create_note(clients["owner_agent"])
    note_id = note["note_id"]
    path = f"/notes/{note_id}"
    headers = {"If-Match": if_match} if if_match is not None else {}

    owner = clients["owner"]
    get_resp = owner.get(path)
    assert get_resp.status_code == 200
    assert get_resp.json()["created_by"] == _OWNER_AGENT.sub
    assert [n["note_id"] for n in owner.get("/notes").json()["data"]] == [note_id]

    _assert_note_not_found(owner.patch(path, json={"body": "Overwritten"}, headers=headers))
    _assert_note_not_found(owner.delete(path, headers=headers))

    _assert_note_not_found(clients["other"].get(path))
