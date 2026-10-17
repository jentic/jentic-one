"""Integration test for the e6858b8a3657 wrongly-superseded overlay repair (#1078).

Runs the migration's repair body against the real registry database (SQLite locally,
Postgres in CI) so its correlated predicate is exercised on both backends. The full
Alembic up/down round trip is pinned on SQLite in
``tests/unit/test_restore_wrongly_superseded_overlays_migration.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from jentic_one.migrations.registry.versions import (
    e6858b8a3657_restore_overlays_wrongly_superseded as repair_migration,
)
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.overlays import Overlay
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration


async def test_repair_restores_only_overlays_whose_revision_is_still_served(
    registry_db: DatabaseSession, clean_registry: None
) -> None:
    """Restores the #1078 row; legit, rollback, cross-API and claimed rows stay deprecated."""
    deprecated_at = datetime(2026, 10, 1, tzinfo=UTC)
    async with registry_db.session() as session:
        doubled = Api(vendor="example-com", name="doubled", version="1.0.0")
        other = Api(vendor="example-com", name="other", version="1.0.0")
        claimed_api = Api(vendor="example-com", name="claimed", version="1.0.0")
        session.add_all([doubled, other, claimed_api])
        await session.flush()
        served = ApiRevision(api_id=doubled.id, state="imported", spec_digest="sha256:served")
        archived = ApiRevision(api_id=doubled.id, state="archived", spec_digest="sha256:old")
        other_served = ApiRevision(api_id=other.id, state="published", spec_digest="sha256:o")
        claimed = ApiRevision(api_id=claimed_api.id, state="imported", spec_digest="sha256:c")
        session.add_all([served, archived, other_served, claimed])
        await session.flush()

        def _deprecated(
            api_id: uuid.UUID, revision_id: uuid.UUID, reason: str = "superseded_by_reimport"
        ) -> Overlay:
            return Overlay(
                api_id=api_id,
                document={},
                status="deprecated",
                confirmed_revision_id=revision_id,
                deprecated_at=deprecated_at,
                deprecated_reason=reason,
            )

        wrong = _deprecated(doubled.id, served.id)
        untouched = [
            _deprecated(doubled.id, archived.id),
            _deprecated(doubled.id, served.id, reason="rollback"),
            _deprecated(doubled.id, other_served.id),
            _deprecated(claimed_api.id, claimed.id),
        ]
        live = Overlay(
            api_id=claimed_api.id,
            document={},
            status="confirmed",
            confirmed_revision_id=claimed.id,
        )
        session.add_all([wrong, *untouched, live])
        await session.commit()
        wrong_id = wrong.id
        untouched_ids = {overlay.id for overlay in untouched}

    async with registry_db.session() as session:
        restored = await session.run_sync(
            lambda sync: repair_migration.restore_wrongly_superseded_overlays(sync.connection())
        )
        await session.commit()
    assert restored == [wrong_id]

    async with registry_db.session() as session:
        rows = {row.id: row for row in (await session.execute(select(Overlay))).scalars().all()}
    assert rows[wrong_id].status == "confirmed"
    assert rows[wrong_id].deprecated_at is None
    assert rows[wrong_id].deprecated_reason is None
    for overlay_id in untouched_ids:
        assert rows[overlay_id].status == "deprecated"
        assert rows[overlay_id].deprecated_at is not None

    # Idempotent: nothing left to restore.
    async with registry_db.session() as session:
        again = await session.run_sync(
            lambda sync: repair_migration.restore_wrongly_superseded_overlays(sync.connection())
        )
    assert again == []
