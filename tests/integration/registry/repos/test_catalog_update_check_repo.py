"""Integration tests for CatalogUpdateCheckRepository + the notify join query.

Verifies the ``catalog_update_checks`` model round-trips against real PostgreSQL
(KSUID id, unique ``local_api_id``, selective ``notified_digest`` write) and that
``ApiRevisionRepository.registered_specs_for_notify`` joins revisions to their API
identity, excludes archived revisions, and de-duplicates per API.
"""

from __future__ import annotations

from datetime import UTC, datetime

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
