"""Integration tests for OverlayRepository."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, update

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.overlays import Overlay
from jentic_one.registry.repos.overlay_repo import OverlayRepository
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import OverlayStatus
from jentic_one.shared.pagination import Cursor

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
            cursor=Cursor(timestamp=last.created_at, id=last.id),
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


async def test_get_live_confirmed_for_api_ignores_link_lag(
    registry_db: DatabaseSession, sample_api: Api
) -> None:
    """get_live_confirmed_for_api returns the CONFIRMED overlay even with a NULL link.

    Regression: the Flow-3 sweep enriches a ``conflicts_overlay`` event with the overlay
    id via this lookup. ``confirmed_revision_id`` is linked *lazily* by the materialize
    path, so it can be NULL while the overlay is already CONFIRMED and its revision served.
    The lookup must key on ``(api_id, status==CONFIRMED)`` — not the revision link — or the
    event drops the overlay id (caught by the manual flywheel E2E, missed by mocked units).
    """
    async with registry_db.session() as session:
        overlay = await OverlayRepository.create(
            session, api_id=sample_api.id, document={"x": 1}, created_by="usr_test"
        )
        # CONFIRMED but not yet linked to a materialized revision (the lazy-link window).
        await OverlayRepository.set_status(
            session, overlay.id, OverlayStatus.CONFIRMED, confirmed_at=datetime.now(UTC)
        )
        await session.commit()
        overlay_id = overlay.id

    async with registry_db.session() as session:
        found = await OverlayRepository.get_live_confirmed_for_api(session, sample_api.id)
        assert found is not None and found.id == overlay_id
        assert found.confirmed_revision_id is None  # link still lagging

    # A deprecated overlay is not "live".
    async with registry_db.session() as session:
        await OverlayRepository.set_status(
            session, overlay_id, OverlayStatus.DEPRECATED, deprecated_at=datetime.now(UTC)
        )
        await session.commit()

    async with registry_db.session() as session:
        assert await OverlayRepository.get_live_confirmed_for_api(session, sample_api.id) is None


async def test_get_live_confirmed_for_api_prefers_live_link_over_recency(
    registry_db: DatabaseSession, sample_api: Api
) -> None:
    """With 2+ CONFIRMED overlays, prefer the one backing the current revision.

    The confirm path does not deprecate a prior CONFIRMED overlay, so stacking confirm A
    then confirm B leaves both CONFIRMED. The sweep's deep-link must point at the overlay
    whose materialization *is* the served revision — keyed on
    ``confirmed_revision_id == Api.current_revision_id`` — not merely the newest/oldest by
    ``confirmed_at``. Here A is confirmed *later* but B backs the current revision, so B wins.
    """
    async with registry_db.session() as session:
        rev_b = ApiRevision(
            api_id=sample_api.id, state="imported", spec_digest="sha256:b", source_type="url"
        )
        session.add(rev_b)
        await session.flush()
        rev_b_id = rev_b.id
        await session.execute(
            update(Api).where(Api.id == sample_api.id).values(current_revision_id=rev_b_id)
        )

        overlay_a = await OverlayRepository.create(
            session, api_id=sample_api.id, document={"a": 1}, created_by="usr_test"
        )
        overlay_b = await OverlayRepository.create(
            session, api_id=sample_api.id, document={"b": 1}, created_by="usr_test"
        )
        # B backs the current revision but was confirmed EARLIER; A is confirmed LATER but is
        # the superseded/stacked overlay. Newest-confirmed would wrongly pick A.
        await OverlayRepository.set_status(
            session,
            overlay_b.id,
            OverlayStatus.CONFIRMED,
            confirmed_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        await OverlayRepository.set_confirmed_revision(session, overlay_b.id, rev_b_id)
        await OverlayRepository.set_status(
            session,
            overlay_a.id,
            OverlayStatus.CONFIRMED,
            confirmed_at=datetime(2026, 6, 1, tzinfo=UTC),
        )
        await session.commit()
        overlay_b_id = overlay_b.id

    async with registry_db.session() as session:
        found = await OverlayRepository.get_live_confirmed_for_api(session, sample_api.id)
        assert found is not None and found.id == overlay_b_id


@pytest.mark.parametrize(
    ("linked_state", "cas", "expected_rows"),
    [
        # The pinned confirmed revision changed under the caller (a re-materialize relinked).
        ("archived", {"expected_confirmed_revision_id": "other"}, 0),
        # NULL-safe: "must still be unlinked" fails once a link lands.
        ("archived", {"expected_confirmed_revision_id": None}, 0),
        # The linked revision is active (served) again — the demote must not fire.
        ("imported", {"require_confirmed_revision_inactive": True}, 0),
        ("published", {"require_confirmed_revision_inactive": True}, 0),
        # The overlay moved to (or never lived on) the expected API row.
        ("archived", {"expected_api_id": "other"}, 0),
        # Every guard holds: the CAS demotes.
        (
            "archived",
            {
                "expected_api_id": "own",
                "expected_confirmed_revision_id": "own",
                "require_confirmed_revision_inactive": True,
            },
            1,
        ),
    ],
)
async def test_set_status_cas_rechecks_guarded_fields(
    registry_db: DatabaseSession,
    sample_api: Api,
    linked_state: str,
    cas: dict[str, object],
    expected_rows: int,
) -> None:
    """#1078: the A4b demote's CAS re-checks the fields its guard read, atomically.

    A concurrent change between the guard's read and the UPDATE (a relink, a revision
    becoming active again, a different API row) makes the CAS match nothing.
    """
    async with registry_db.session() as session:
        revision = ApiRevision(api_id=sample_api.id, state=linked_state, spec_digest="sha256:ln")
        session.add(revision)
        await session.flush()
        overlay = await OverlayRepository.create(
            session, api_id=sample_api.id, document={"cas": 1}, created_by="usr_test"
        )
        await session.execute(
            update(Overlay)
            .where(Overlay.id == overlay.id)
            .values(status=OverlayStatus.CONFIRMED, confirmed_revision_id=revision.id)
        )
        await session.commit()
        overlay_id, revision_id = overlay.id, revision.id

    resolved = {
        "own": {"expected_api_id": sample_api.id, "expected_confirmed_revision_id": revision_id},
        "other": {"expected_api_id": uuid.uuid4(), "expected_confirmed_revision_id": uuid.uuid4()},
    }
    kwargs = {
        key: resolved[value][key] if isinstance(value, str) else value for key, value in cas.items()
    }
    async with registry_db.session() as session:
        rows = await OverlayRepository.set_status(
            session,
            overlay_id,
            OverlayStatus.DEPRECATED,
            deprecated_at=datetime.now(UTC),
            expected_status=OverlayStatus.CONFIRMED,
            **kwargs,  # type: ignore[arg-type]
        )
        await session.commit()
    assert rows == expected_rows

    async with registry_db.session() as session:
        fetched = await OverlayRepository.get_by_id(session, overlay_id)
    assert fetched is not None
    assert fetched.status == ("deprecated" if expected_rows else "confirmed")


@pytest.mark.parametrize(
    ("current_origin", "expected_rows"),
    [
        # A re-materialize committed its overlay-origin revision as current but has not
        # linked it yet: the overlay is served again, so the demote must not fire.
        ("overlay", 0),
        # The fresh upstream is served (every legitimate supersede): the demote fires.
        ("catalog", 1),
        (None, 1),
        # No current revision at all: nothing overlay-origin is served.
        ("no_current", 1),
    ],
)
async def test_set_status_cas_requires_api_serving_non_overlay(
    registry_db: DatabaseSession,
    sample_api: Api,
    current_origin: str | None,
    expected_rows: int,
) -> None:
    """#1078: ``require_api_serves_non_overlay`` keys on the overlay's API current revision.

    The overlay still links its archived revision, so every overlay-row predicate holds;
    only the API row shows that a re-materialize landed between the guard and the CAS.
    """
    async with registry_db.session() as session:
        archived = ApiRevision(api_id=sample_api.id, state="archived", spec_digest="sha256:arc")
        session.add(archived)
        await session.flush()
        if current_origin != "no_current":
            current = ApiRevision(
                api_id=sample_api.id,
                state="imported",
                origin=current_origin,
                spec_digest="sha256:cur",
            )
            session.add(current)
            await session.flush()
            await session.execute(
                update(Api).where(Api.id == sample_api.id).values(current_revision_id=current.id)
            )
        overlay = await OverlayRepository.create(
            session, api_id=sample_api.id, document={"rm": 1}, created_by="usr_test"
        )
        await session.execute(
            update(Overlay)
            .where(Overlay.id == overlay.id)
            .values(status=OverlayStatus.CONFIRMED, confirmed_revision_id=archived.id)
        )
        await session.commit()
        overlay_id, archived_id = overlay.id, archived.id

    async with registry_db.session() as session:
        rows = await OverlayRepository.set_status(
            session,
            overlay_id,
            OverlayStatus.DEPRECATED,
            deprecated_at=datetime.now(UTC),
            expected_status=OverlayStatus.CONFIRMED,
            expected_api_id=sample_api.id,
            expected_confirmed_revision_id=archived_id,
            require_confirmed_revision_inactive=True,
            require_api_serves_non_overlay=True,
        )
        await session.commit()
    assert rows == expected_rows
