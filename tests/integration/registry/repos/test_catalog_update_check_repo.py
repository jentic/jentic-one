"""Integration tests for CatalogUpdateCheckRepository + the notify join query.

Verifies the ``catalog_update_checks`` model round-trips against real PostgreSQL
(KSUID id, unique ``local_api_id``, selective ``notified_digest`` write) and that
``ApiRevisionRepository.registered_specs_for_notify`` joins revisions to their API
identity, excludes archived revisions, and de-duplicates per API.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.repos.catalog_update_check_repo import CatalogUpdateCheckRepository
from jentic_one.registry.repos.revision_repo import ApiRevisionRepository
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration


async def test_upsert_inserts_then_updates(registry_db: DatabaseSession, sample_api: Api) -> None:
    """First upsert inserts a KSUID-id row; the second updates it in place."""
    now = datetime.now(UTC)
    async with registry_db.session() as session:
        row = await CatalogUpdateCheckRepository.upsert(
            session,
            local_api_id=sample_api.id,
            spec_url="https://example.com/openapi.json",
            etag='"v1"',
            digest="digest-1",
            checked_at=now,
        )
        await session.commit()
        row_id = row.id

    assert row_id.startswith("cuc_")

    async with registry_db.session() as session:
        again = await CatalogUpdateCheckRepository.upsert(
            session,
            local_api_id=sample_api.id,
            spec_url="https://example.com/openapi.json",
            etag='"v2"',
            digest="digest-2",
            checked_at=now,
            notified_digest="digest-2",
            notified_event_class="catalog.update_conflicts_overlay",
        )
        await session.commit()

    assert again.id == row_id  # same row, updated in place
    assert again.last_seen_etag == '"v2"'
    assert again.last_seen_digest == "digest-2"
    assert again.last_notified_digest == "digest-2"
    # The event class the digest fired under round-trips (part of the dedupe key).
    assert again.last_notified_event_class == "catalog.update_conflicts_overlay"


async def test_upsert_does_not_clear_notified_digest_on_none(
    registry_db: DatabaseSession, sample_api: Api
) -> None:
    """A later probe with notified_digest=None keeps the previously-notified value."""
    now = datetime.now(UTC)
    async with registry_db.session() as session:
        await CatalogUpdateCheckRepository.upsert(
            session,
            local_api_id=sample_api.id,
            spec_url="https://example.com/openapi.json",
            etag='"v1"',
            digest="digest-1",
            checked_at=now,
            notified_digest="digest-1",
        )
        await session.commit()

    async with registry_db.session() as session:
        row = await CatalogUpdateCheckRepository.upsert(
            session,
            local_api_id=sample_api.id,
            spec_url="https://example.com/openapi.json",
            etag='"v1"',
            digest=None,
            checked_at=now,
        )
        await session.commit()

    assert row.last_notified_digest == "digest-1"


async def test_registered_specs_for_notify_joins_identity_and_excludes_archived(
    registry_db: DatabaseSession, sample_api: Api
) -> None:
    """The notify query returns identity+digest for non-archived, source_url'd revisions."""
    async with registry_db.session() as session:
        session.add(
            ApiRevision(
                api_id=sample_api.id,
                state="published",
                spec_digest="sha256:live",
                source_type="url",
                source_url="https://example.com/openapi.json",
            )
        )
        # An archived revision from a different URL must be excluded.
        session.add(
            ApiRevision(
                api_id=sample_api.id,
                state="archived",
                spec_digest="sha256:old",
                source_type="url",
                source_url="https://example.com/old.json",
            )
        )
        await session.commit()

    async with registry_db.session() as session:
        specs = await ApiRevisionRepository.registered_specs_for_notify(session)

    mine = [s for s in specs if s.api_id == sample_api.id]
    assert len(mine) == 1
    spec = mine[0]
    assert spec.source_url == "https://example.com/openapi.json"
    assert spec.spec_digest == "sha256:live"
    assert spec.vendor == sample_api.vendor
    assert spec.name == sample_api.name
    assert spec.version == sample_api.version
    # Non-overlay revision → no overlay base digest.
    assert spec.overlay_base_digest is None


async def test_registered_specs_for_notify_threads_overlay_base_digest(
    registry_db: DatabaseSession, sample_api: Api
) -> None:
    """An overlay-origin current revision surfaces its overlay_base_digest to the sweep.

    A3 classifies an upstream change as a conflict by comparing the upstream digest to
    the overlay's base — so the notify query must carry ``overlay_base_digest`` through.
    """
    async with registry_db.session() as session:
        rev = ApiRevision(
            api_id=sample_api.id,
            state="published",
            origin="overlay",
            spec_digest="sha256:overlaid",
            overlay_base_digest="sha256:base",
            source_type="url",
            source_url="https://example.com/openapi.json",
        )
        session.add(rev)
        await session.commit()

    async with registry_db.session() as session:
        specs = await ApiRevisionRepository.registered_specs_for_notify(session)

    mine = [s for s in specs if s.api_id == sample_api.id]
    assert len(mine) == 1
    assert mine[0].origin == "overlay"
    assert mine[0].overlay_base_digest == "sha256:base"


async def test_registered_specs_for_notify_picks_deterministic_revision(
    registry_db: DatabaseSession, sample_api: Api
) -> None:
    """With >1 live revision, selection is deterministic: current_revision_id wins,
    else newest created_at — never DB-row-order-dependent (would cause spurious
    notifies against a stale digest/source_url)."""
    older = ApiRevision(
        api_id=sample_api.id,
        state="published",
        spec_digest="sha256:older",
        source_type="url",
        source_url="https://example.com/older.json",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    newer = ApiRevision(
        api_id=sample_api.id,
        state="draft",
        spec_digest="sha256:newer",
        source_type="url",
        source_url="https://example.com/newer.json",
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    async with registry_db.session() as session:
        session.add_all([older, newer])
        await session.commit()
        newer_id, older_id = newer.id, older.id

    # No current_revision_id → newest created_at wins.
    async with registry_db.session() as session:
        specs = await ApiRevisionRepository.registered_specs_for_notify(session)
    mine = [s for s in specs if s.api_id == sample_api.id]
    assert len(mine) == 1
    assert mine[0].spec_digest == "sha256:newer"
    assert mine[0].source_url == "https://example.com/newer.json"

    # current_revision_id set to the OLDER revision → it overrides recency.
    async with registry_db.session() as session:
        api = await session.get(Api, sample_api.id)
        assert api is not None
        api.current_revision_id = older_id
        await session.commit()

    async with registry_db.session() as session:
        specs = await ApiRevisionRepository.registered_specs_for_notify(session)
    mine = [s for s in specs if s.api_id == sample_api.id]
    assert len(mine) == 1
    assert mine[0].spec_digest == "sha256:older"
    assert mine[0].source_url == "https://example.com/older.json"
    assert newer_id != older_id  # sanity: two distinct revisions existed


async def _seed_outdated(
    registry_db: DatabaseSession, api: Api, *, served_digest: str, notified_digest: str
) -> None:
    """Seed a served revision + a check row notified at a different digest (outdated)."""
    now = datetime.now(UTC)
    async with registry_db.session() as session:
        rev = ApiRevision(
            api_id=api.id,
            state="published",
            spec_digest=served_digest,
            source_type="url",
            source_url="https://example.com/openapi.json",
        )
        session.add(rev)
        await session.flush()
        fetched = await session.get(Api, api.id)
        assert fetched is not None
        fetched.current_revision_id = rev.id
        await CatalogUpdateCheckRepository.upsert(
            session,
            local_api_id=api.id,
            spec_url="https://example.com/openapi.json",
            etag='"v1"',
            digest=notified_digest,
            checked_at=now,
            notified_digest=notified_digest,
            notified_event_class="catalog.update_available",
        )
        await session.commit()


async def test_snooze_excludes_from_outdated_and_unsnooze_restores(
    registry_db: DatabaseSession, sample_api: Api
) -> None:
    """An active snooze on the notified digest drops the API from the outdated set (C1).

    ``include_snoozed=True`` still lists it, and ``unsnooze`` restores it.
    """
    await _seed_outdated(
        registry_db, sample_api, served_digest="sha256:served", notified_digest="sha256:up"
    )
    now = datetime.now(UTC)

    async with registry_db.session() as session:
        ids = await CatalogUpdateCheckRepository.outdated_api_ids(session, now=now)
        assert sample_api.id in ids

    # Snooze the exact notified digest → excluded from the default outdated set.
    async with registry_db.session() as session:
        rows = await CatalogUpdateCheckRepository.snooze(
            session, sample_api.id, digest="sha256:up", until=None
        )
        await session.commit()
        assert rows == 1

    async with registry_db.session() as session:
        assert sample_api.id not in await CatalogUpdateCheckRepository.outdated_api_ids(
            session, now=now
        )
        # ...but include_snoozed still surfaces it.
        assert sample_api.id in await CatalogUpdateCheckRepository.outdated_api_ids(
            session, now=now, include_snoozed=True
        )

    async with registry_db.session() as session:
        rows = await CatalogUpdateCheckRepository.unsnooze(session, sample_api.id)
        await session.commit()
        assert rows == 1

    async with registry_db.session() as session:
        ids = await CatalogUpdateCheckRepository.outdated_api_ids(session, now=now)
        assert sample_api.id in ids


async def test_snooze_does_not_hide_a_newer_digest(
    registry_db: DatabaseSession, sample_api: Api
) -> None:
    """Snoozing digest A must NOT hide a genuinely newer digest B (auto-clear on newer)."""
    await _seed_outdated(
        registry_db, sample_api, served_digest="sha256:served", notified_digest="sha256:A"
    )
    now = datetime.now(UTC)
    async with registry_db.session() as session:
        await CatalogUpdateCheckRepository.snooze(
            session, sample_api.id, digest="sha256:A", until=None
        )
        await session.commit()

    # A newer upstream lands: the sweep records a new last_notified_digest = B.
    async with registry_db.session() as session:
        await CatalogUpdateCheckRepository.upsert(
            session,
            local_api_id=sample_api.id,
            spec_url="https://example.com/openapi.json",
            etag='"v2"',
            digest="sha256:B",
            checked_at=now,
            notified_digest="sha256:B",
            notified_event_class="catalog.update_available",
        )
        await session.commit()

    # snoozed_digest (A) no longer matches last_notified_digest (B) → outdated again.
    async with registry_db.session() as session:
        ids = await CatalogUpdateCheckRepository.outdated_api_ids(session, now=now)
        assert sample_api.id in ids


async def test_expired_snooze_no_longer_excludes(
    registry_db: DatabaseSession, sample_api: Api
) -> None:
    """A snooze whose ``snoozed_until`` is in the past no longer suppresses the badge."""
    await _seed_outdated(
        registry_db, sample_api, served_digest="sha256:served", notified_digest="sha256:up"
    )
    now = datetime.now(UTC)
    past = datetime(2000, 1, 1, tzinfo=UTC)
    async with registry_db.session() as session:
        await CatalogUpdateCheckRepository.snooze(
            session, sample_api.id, digest="sha256:up", until=past
        )
        await session.commit()

    async with registry_db.session() as session:
        ids = await CatalogUpdateCheckRepository.outdated_api_ids(session, now=now)
        assert sample_api.id in ids


async def test_expired_snooze_re_lights_without_explicit_now(
    registry_db: DatabaseSession, sample_api: Api
) -> None:
    """A lapsed time-boxed snooze re-lights even when the caller doesn't thread ``now``.

    Regression: the per-API surfaces (``/apis`` list, single-API view) and the single
    catalog ``get()`` call ``outdated_api_ids``/``outdated_spec_urls`` *without* ``now``.
    ``_not_snoozed`` used to treat any non-null ``snoozed_until`` as still-active when
    ``now`` was None, so an expired time-boxed snooze stayed hidden forever on those
    surfaces. ``_not_snoozed`` now defaults ``now`` to the current UTC time, so a lapsed
    snooze correctly re-enters the outdated set with no clock threaded.
    """
    await _seed_outdated(
        registry_db, sample_api, served_digest="sha256:served", notified_digest="sha256:up"
    )
    past = datetime(2000, 1, 1, tzinfo=UTC)
    async with registry_db.session() as session:
        await CatalogUpdateCheckRepository.snooze(
            session, sample_api.id, digest="sha256:up", until=past
        )
        await session.commit()

    async with registry_db.session() as session:
        # No `now` passed — the default clock must still see the snooze as expired.
        ids = await CatalogUpdateCheckRepository.outdated_api_ids(session)
        assert sample_api.id in ids
        urls = await CatalogUpdateCheckRepository.outdated_spec_urls(session)
        assert "https://example.com/openapi.json" in urls


async def test_future_snooze_still_excludes_without_explicit_now(
    registry_db: DatabaseSession, sample_api: Api
) -> None:
    """A still-active (future ``snoozed_until``) snooze stays hidden under the default clock."""
    await _seed_outdated(
        registry_db, sample_api, served_digest="sha256:served", notified_digest="sha256:up"
    )
    future = datetime.now(UTC) + timedelta(days=1)
    async with registry_db.session() as session:
        await CatalogUpdateCheckRepository.snooze(
            session, sample_api.id, digest="sha256:up", until=future
        )
        await session.commit()

    async with registry_db.session() as session:
        ids = await CatalogUpdateCheckRepository.outdated_api_ids(session)
        assert sample_api.id not in ids
