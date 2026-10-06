"""Web tests for note visibility on the ``/notes`` routes.

A note is visible to its creator and to ``org:admin``. Reads, updates and
deletes by any other caller return a 404 problem document, including when the
request carries an ``If-Match`` revision, and leave the note unchanged.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import delete

from jentic_one.registry.core.schema.notes import Note
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from tests.web.registry.conftest import _build_app_as

pytestmark = pytest.mark.integration

_OWNER = Identity(sub="usr_web_note_owner", email="note-owner@test.local")
_OTHER = Identity(sub="usr_web_note_other", email="note-other@test.local")
_ADMIN = Identity(
    sub="usr_web_note_admin", email="note-admin@test.local", permissions=["org:admin"]
)


@pytest.fixture()
async def _clean_notes(web_context: Context) -> AsyncGenerator[None, None]:
    subs = [_OWNER.sub, _OTHER.sub, _ADMIN.sub]

    async def _wipe() -> None:
        async with web_context.registry_db.session() as session:
            await session.execute(delete(Note).where(Note.created_by.in_(subs)))
            await session.commit()

    await _wipe()
    yield
    await _wipe()


def _client_as(ctx: Context, identity: Identity) -> TestClient:
    app = _build_app_as(ctx, identity)
    return TestClient(app, headers={"Authorization": "Bearer test-token"})


@pytest.fixture()
def clients(web_context: Context, _clean_notes: None) -> Iterator[dict[str, TestClient]]:
    with (
        _client_as(web_context, _OWNER) as owner,
        _client_as(web_context, _OTHER) as other,
        _client_as(web_context, _ADMIN) as admin,
    ):
        yield {"owner": owner, "other": other, "admin": admin}


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
