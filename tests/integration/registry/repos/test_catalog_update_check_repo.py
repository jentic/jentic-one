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
        )
        await session.commit()

    assert again.id == row_id  # same row, updated in place
    assert again.last_seen_etag == '"v2"'
    assert again.last_seen_digest == "digest-2"
    assert again.last_notified_digest == "digest-2"


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
