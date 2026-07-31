"""Integration tests for OverlayRepository."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete

from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.repos.overlay_repo import OverlayRepository
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import OverlayStatus

pytestmark = pytest.mark.integration


async def test_create_and_read_back(registry_db: DatabaseSession, sample_api: Api) -> None:
    """create persists all fields and get_for_api reads them back."""
    doc = {"overlay": "1.0", "actions": [{"target": "/info/title", "update": "New"}]}

    async with registry_db.session() as session:
        overlay = await OverlayRepository.create(
            session,
            api_id=sample_api.id,
            document=doc,
            target_revision_id=None,
            contributed_by="test-agent",
            created_by="usr_test",
        )
        await session.commit()
        overlay_id = overlay.id

    assert overlay_id.startswith("ovr_")

    async with registry_db.session() as session:
        fetched = await OverlayRepository.get_for_api(session, sample_api.id, overlay_id)

    assert fetched is not None
    assert fetched.id == overlay_id
    assert fetched.api_id == sample_api.id
    assert fetched.document == doc
    assert fetched.status == "pending"
    assert fetched.contributed_by == "test-agent"
    assert fetched.target_revision_id is None
    assert fetched.created_at is not None


async def test_get_for_api_wrong_api_returns_none(
    registry_db: DatabaseSession, sample_api: Api
) -> None:
    """get_for_api returns None when queried with a different api_id."""
    async with registry_db.session() as session:
        overlay = await OverlayRepository.create(
            session, api_id=sample_api.id, document={"x": 1}, created_by="usr_test"
        )
        await session.commit()
        overlay_id = overlay.id

    async with registry_db.session() as session:
        result = await OverlayRepository.get_for_api(session, uuid.uuid4(), overlay_id)

    assert result is None


async def test_get_for_api_wrong_overlay_id_returns_none(
    registry_db: DatabaseSession, sample_api: Api
) -> None:
    """get_for_api returns None for a non-existent overlay_id."""
    async with registry_db.session() as session:
        result = await OverlayRepository.get_for_api(
            session, sample_api.id, "ovr_0000000000000000000000"
        )

    assert result is None


async def test_list_page_newest_first(registry_db: DatabaseSession, sample_api: Api) -> None:
    """list_page returns overlays in newest-first order."""
    for i in range(3):
        async with registry_db.session() as session:
            await OverlayRepository.create(
                session, api_id=sample_api.id, document={"seq": i}, created_by="usr_test"
            )
            await session.commit()
        await asyncio.sleep(0.01)

    async with registry_db.session() as session:
        page = await OverlayRepository.list_page(session, api_id=sample_api.id)

    assert len(page) == 3
    assert page[0].document["seq"] == 2
    assert page[1].document["seq"] == 1
    assert page[2].document["seq"] == 0


async def test_list_page_status_filter(registry_db: DatabaseSession, sample_api: Api) -> None:
    """list_page filters by status when provided."""
    async with registry_db.session() as session:
        o1 = await OverlayRepository.create(
            session, api_id=sample_api.id, document={"s": "pending"}, created_by="usr_test"
        )
        await session.commit()

    async with registry_db.session() as session:
        await OverlayRepository.set_status(
            session,
            o1.id,
            OverlayStatus.CONFIRMED,
            confirmed_at=datetime.now(UTC),
        )
        await session.commit()

    async with registry_db.session() as session:
        await OverlayRepository.create(
            session, api_id=sample_api.id, document={"s": "pending2"}, created_by="usr_test"
        )
        await session.commit()

    async with registry_db.session() as session:
        pending = await OverlayRepository.list_page(session, api_id=sample_api.id, status="pending")
        confirmed = await OverlayRepository.list_page(
            session, api_id=sample_api.id, status="confirmed"
        )

    assert len(pending) == 1
    assert pending[0].document["s"] == "pending2"
    assert len(confirmed) == 1


async def test_list_page_cursor_pagination(registry_db: DatabaseSession, sample_api: Api) -> None:
    """list_page paginates correctly using cursor."""
    for i in range(5):
        async with registry_db.session() as session:
            await OverlayRepository.create(
                session, api_id=sample_api.id, document={"seq": i}, created_by="usr_test"
            )
            await session.commit()
        await asyncio.sleep(0.01)

    async with registry_db.session() as session:
        first_page = await OverlayRepository.list_page(session, api_id=sample_api.id, limit=3)

    assert len(first_page) == 3
    last = first_page[-1]

    async with registry_db.session() as session:
        second_page = await OverlayRepository.list_page(
            session,
            api_id=sample_api.id,
            limit=3,
            cursor_created_at=last.created_at,
            cursor_id=last.id,
        )

    assert len(second_page) == 2
    all_ids = [o.id for o in first_page] + [o.id for o in second_page]
    assert len(set(all_ids)) == 5


async def test_update_fields(registry_db: DatabaseSession, sample_api: Api) -> None:
    """update_fields persists document and target_revision_id changes."""
    new_rev_id = uuid.uuid4()

    async with registry_db.session() as session:
        overlay = await OverlayRepository.create(
            session, api_id=sample_api.id, document={"v": 1}, created_by="usr_test"
        )
        await session.commit()
        overlay_id = overlay.id

    async with registry_db.session() as session:
        rows = await OverlayRepository.update_fields(
            session, overlay_id, document={"v": 2}, target_revision_id=new_rev_id
        )
        await session.commit()

    assert rows == 1

    async with registry_db.session() as session:
        updated = await OverlayRepository.get_for_api(session, sample_api.id, overlay_id)

    assert updated is not None
    assert updated.document == {"v": 2}
    assert updated.target_revision_id == new_rev_id
    assert updated.updated_at is not None


async def test_set_status_confirmed(registry_db: DatabaseSession, sample_api: Api) -> None:
    """set_status transitions to confirmed with timestamp and execution id."""
    now = datetime.now(UTC)

    async with registry_db.session() as session:
        overlay = await OverlayRepository.create(
            session, api_id=sample_api.id, document={"c": 1}, created_by="usr_test"
        )
        await session.commit()
        overlay_id = overlay.id

    async with registry_db.session() as session:
        rows = await OverlayRepository.set_status(
            session,
            overlay_id,
            OverlayStatus.CONFIRMED,
            confirmed_at=now,
            confirmed_by_execution_id="exec-123",
        )
        await session.commit()

    assert rows == 1

    async with registry_db.session() as session:
        fetched = await OverlayRepository.get_for_api(session, sample_api.id, overlay_id)

    assert fetched is not None
    assert fetched.status == "confirmed"
    assert fetched.confirmed_at is not None
    assert fetched.confirmed_by_execution_id == "exec-123"


async def test_set_status_deprecated(registry_db: DatabaseSession, sample_api: Api) -> None:
    """set_status transitions to deprecated with timestamp."""
    now = datetime.now(UTC)

    async with registry_db.session() as session:
        overlay = await OverlayRepository.create(
            session, api_id=sample_api.id, document={"d": 1}, created_by="usr_test"
        )
        await session.commit()
        overlay_id = overlay.id

    async with registry_db.session() as session:
        rows = await OverlayRepository.set_status(
            session, overlay_id, OverlayStatus.DEPRECATED, deprecated_at=now
        )
        await session.commit()

    assert rows == 1

    async with registry_db.session() as session:
        fetched = await OverlayRepository.get_for_api(session, sample_api.id, overlay_id)

    assert fetched is not None
    assert fetched.status == "deprecated"
    assert fetched.deprecated_at is not None


async def test_set_confirmed_revision(registry_db: DatabaseSession, sample_api: Api) -> None:
    """set_confirmed_revision back-links the materialized revision onto the overlay."""
    revision_id = uuid.uuid4()

    async with registry_db.session() as session:
        overlay = await OverlayRepository.create(
            session, api_id=sample_api.id, document={"m": 1}, created_by="usr_test"
        )
        await session.commit()
        overlay_id = overlay.id

    async with registry_db.session() as session:
        rows = await OverlayRepository.set_confirmed_revision(session, overlay_id, revision_id)
        await session.commit()

    assert rows == 1

    async with registry_db.session() as session:
        fetched = await OverlayRepository.get_for_api(session, sample_api.id, overlay_id)

    assert fetched is not None
    assert fetched.confirmed_revision_id == revision_id
    assert fetched.updated_at is not None


async def test_set_confirmed_revision_records_superseded(
    registry_db: DatabaseSession, sample_api: Api
) -> None:
    """set_confirmed_revision persists the superseded revision when supplied."""
    revision_id = uuid.uuid4()
    superseded_id = uuid.uuid4()

    async with registry_db.session() as session:
        overlay = await OverlayRepository.create(
            session, api_id=sample_api.id, document={"m": 1}, created_by="usr_test"
        )
        await session.commit()
        overlay_id = overlay.id

    async with registry_db.session() as session:
        rows = await OverlayRepository.set_confirmed_revision(
            session, overlay_id, revision_id, superseded_revision_id=superseded_id
        )
        await session.commit()

    assert rows == 1

    async with registry_db.session() as session:
        fetched = await OverlayRepository.get_for_api(session, sample_api.id, overlay_id)

    assert fetched is not None
    assert fetched.confirmed_revision_id == revision_id
    assert fetched.superseded_revision_id == superseded_id


async def test_set_confirmed_revision_none_superseded_does_not_clobber(
    registry_db: DatabaseSession, sample_api: Api
) -> None:
    """A later relink without a superseded id must not NULL an already-captured one.

    The recovery/relink path (duplicate re-ingest) doesn't know the superseded
    revision, so it passes None — that must leave a previously-recorded
    superseded_revision_id intact rather than overwriting it with NULL.
    """
    first_rev, superseded_id, relink_rev = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    async with registry_db.session() as session:
        overlay = await OverlayRepository.create(
            session, api_id=sample_api.id, document={"m": 1}, created_by="usr_test"
        )
        await session.commit()
        overlay_id = overlay.id

    async with registry_db.session() as session:
        await OverlayRepository.set_confirmed_revision(
            session, overlay_id, first_rev, superseded_revision_id=superseded_id
        )
        await session.commit()

    # A relink that doesn't carry the superseded id (None) must preserve it.
    async with registry_db.session() as session:
        await OverlayRepository.set_confirmed_revision(session, overlay_id, relink_rev)
        await session.commit()

    async with registry_db.session() as session:
        fetched = await OverlayRepository.get_for_api(session, sample_api.id, overlay_id)

    assert fetched is not None
    assert fetched.confirmed_revision_id == relink_rev
    assert fetched.superseded_revision_id == superseded_id  # preserved, not clobbered


async def test_set_confirmed_revision_missing_overlay_returns_zero(
    registry_db: DatabaseSession, sample_api: Api
) -> None:
    """A no-op update (unknown overlay) reports zero rows rather than raising."""
    async with registry_db.session() as session:
        rows = await OverlayRepository.set_confirmed_revision(
            session, "ovr_0000000000000000000000", uuid.uuid4()
        )
        await session.commit()

    assert rows == 0


async def test_cascade_delete(registry_db: DatabaseSession, sample_api: Api) -> None:
    """Deleting the parent Api cascades to delete its overlays."""
    async with registry_db.session() as session:
        overlay = await OverlayRepository.create(
            session, api_id=sample_api.id, document={"del": 1}, created_by="usr_test"
        )
        await session.commit()
        overlay_id = overlay.id

    async with registry_db.session() as session:
        await session.execute(delete(Api).where(Api.id == sample_api.id))
        await session.commit()

    async with registry_db.session() as session:
        fetched = await OverlayRepository.get_for_api(session, sample_api.id, overlay_id)

    assert fetched is None
