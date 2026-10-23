"""Integration tests for NoteRepository."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete

from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.repos.note_repo import UNSET, NoteRepository
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration


async def test_create_and_read_back(registry_db: DatabaseSession, sample_api: Api) -> None:
    """create persists all fields and get_by_id reads them back."""
    async with registry_db.session() as session:
        note = await NoteRepository.create(
            session,
            resource_api_id=sample_api.id,
            body="Test note body",
            type="execution_feedback",
            confidence="observed",
            confidence_source="client",
            source="agent",
            created_by="user@test.com",
            related_execution_id="exec_123",
        )
        await session.commit()
        note_id = note.id

    assert note_id.startswith("note_")

    async with registry_db.session() as session:
        fetched = await NoteRepository.get_by_id(session, note_id)

    assert fetched is not None
    assert fetched.id == note_id
    assert fetched.resource_api_id == sample_api.id
    assert fetched.body == "Test note body"
    assert fetched.type == "execution_feedback"
    assert fetched.confidence == "observed"
    assert fetched.confidence_source == "client"
    assert fetched.source == "agent"
    assert fetched.created_by == "user@test.com"
    assert fetched.related_execution_id == "exec_123"
    assert fetched.revision == 1
    assert fetched.created_at is not None
    assert fetched.updated_at is not None
    # The api relationship is eager-loaded so callers can map it after the
    # session closes without tripping the lazy="raise" guard.
    assert fetched.api is not None
    assert fetched.api.id == sample_api.id


async def test_list_page_newest_first(registry_db: DatabaseSession, sample_api: Api) -> None:
    """list_page returns notes in newest-first order."""
    for i in range(3):
        async with registry_db.session() as session:
            await NoteRepository.create(
                session,
                resource_api_id=sample_api.id,
                body=f"Note {i}",
                created_by="user@test.com",
            )
            await session.commit()
        await asyncio.sleep(0.01)

    async with registry_db.session() as session:
        page = await NoteRepository.list_page(session, api_ids=[sample_api.id])

    assert len(page) == 3
    assert page[0].body == "Note 2"
    assert page[1].body == "Note 1"
    assert page[2].body == "Note 0"


async def test_list_page_filters(registry_db: DatabaseSession, sample_api: Api) -> None:
    """list_page filters by type and created_by."""
    async with registry_db.session() as session:
        await NoteRepository.create(
            session,
            resource_api_id=sample_api.id,
            body="A",
            type="execution_feedback",
            created_by="alice@test.com",
        )
        await NoteRepository.create(
            session,
            resource_api_id=sample_api.id,
            body="B",
            type="usage_hint",
            created_by="bob@test.com",
        )
        await session.commit()

    async with registry_db.session() as session:
        by_type = await NoteRepository.list_page(session, type="execution_feedback")
        by_creator = await NoteRepository.list_page(session, created_by="bob@test.com")

    assert len(by_type) == 1
    assert by_type[0].body == "A"
    assert len(by_creator) == 1
    assert by_creator[0].body == "B"


async def test_list_page_cursor_pagination(registry_db: DatabaseSession, sample_api: Api) -> None:
    """list_page paginates correctly using cursor."""
    for i in range(5):
        async with registry_db.session() as session:
            await NoteRepository.create(
                session,
                resource_api_id=sample_api.id,
                body=f"Note {i}",
                created_by="user@test.com",
            )
            await session.commit()
        await asyncio.sleep(0.01)

    async with registry_db.session() as session:
        first_page = await NoteRepository.list_page(session, api_ids=[sample_api.id], limit=3)

    assert len(first_page) == 3
    last = first_page[-1]

    async with registry_db.session() as session:
        second_page = await NoteRepository.list_page(
            session,
            api_ids=[sample_api.id],
            limit=3,
            cursor=(last.created_at, last.id),
        )

    assert len(second_page) == 2
    all_ids = [n.id for n in first_page] + [n.id for n in second_page]
    assert len(set(all_ids)) == 5


async def test_update_fields_increments_revision(
    registry_db: DatabaseSession, sample_api: Api
) -> None:
    """update_fields bumps revision and sets updated_at."""
    async with registry_db.session() as session:
        note = await NoteRepository.create(
            session,
            resource_api_id=sample_api.id,
            body="Original",
            created_by="user@test.com",
        )
        await session.commit()
        note_id = note.id

    async with registry_db.session() as session:
        fetched = await NoteRepository.get_by_id(session, note_id)
        assert fetched is not None
        updated = await NoteRepository.update_fields(session, fetched, body="Updated")
        await session.commit()

    assert updated.revision == 2
    assert updated.body == "Updated"
    assert updated.updated_at is not None


async def test_update_fields_unset_vs_explicit_none(
    registry_db: DatabaseSession, sample_api: Api
) -> None:
    """update_fields distinguishes UNSET (no-op) from None (clear)."""
    async with registry_db.session() as session:
        note = await NoteRepository.create(
            session,
            resource_api_id=sample_api.id,
            body="Body",
            type="execution_feedback",
            confidence="observed",
            created_by="user@test.com",
        )
        await session.commit()
        note_id = note.id

    async with registry_db.session() as session:
        fetched = await NoteRepository.get_by_id(session, note_id)
        assert fetched is not None
        updated = await NoteRepository.update_fields(
            session,
            fetched,
            type=None,
            confidence=UNSET,
        )
        await session.commit()

    assert updated.type is None
    assert updated.confidence == "observed"


async def test_delete_removes_row(registry_db: DatabaseSession, sample_api: Api) -> None:
    """delete removes the note from the database."""
    async with registry_db.session() as session:
        note = await NoteRepository.create(
            session,
            resource_api_id=sample_api.id,
            body="To delete",
            created_by="user@test.com",
        )
        await session.commit()
        note_id = note.id

    async with registry_db.session() as session:
        fetched = await NoteRepository.get_by_id(session, note_id)
        assert fetched is not None
        await NoteRepository.delete(session, fetched)
        await session.commit()

    async with registry_db.session() as session:
        fetched = await NoteRepository.get_by_id(session, note_id)

    assert fetched is None


async def test_cascade_delete_from_api(registry_db: DatabaseSession, sample_api: Api) -> None:
    """Deleting the parent Api cascades to delete its notes."""
    async with registry_db.session() as session:
        note = await NoteRepository.create(
            session,
            resource_api_id=sample_api.id,
            body="Will be cascaded",
            created_by="user@test.com",
        )
        await session.commit()
        note_id = note.id

    async with registry_db.session() as session:
        await session.execute(delete(Api).where(Api.id == sample_api.id))
        await session.commit()

    async with registry_db.session() as session:
        fetched = await NoteRepository.get_by_id(session, note_id)

    assert fetched is None
