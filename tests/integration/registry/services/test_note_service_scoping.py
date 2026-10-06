"""Integration tests for caller visibility on note reads and mutations.

A note is visible to its creator and to ``org:admin``. Updates and deletes apply
the same visibility as reads: a note the caller cannot see is reported as not
found, before any ``If-Match`` revision check, and the row is left unchanged.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.notes import Note
from jentic_one.registry.services.errors import NoteNotFoundError
from jentic_one.registry.services.note_service import NoteService, NoteView
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession

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
