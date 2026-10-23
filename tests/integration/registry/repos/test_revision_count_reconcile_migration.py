"""Integration tests for the f9a0b1c2d3e4 revision-count reconcile migration (#1980).

Seeds APIs whose ``revision_count`` drifted from their ``api_revisions`` rows,
runs the migration's ``reconcile_revision_counts`` body against the real
database, and asserts every count equals its row count, untouched rows stay
untouched, and a second run is a no-op.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from jentic_one.migrations.registry.versions import (
    f9a0b1c2d3e4_reconcile_api_revision_counts as reconcile_migration,
)
from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration


async def _seed_api(
    registry_db: DatabaseSession, *, name: str, revision_count: int, revisions: int
) -> None:
    async with registry_db.session() as session:
        api = Api(
            vendor="reconcile.example.com",
            name=name,
            version="1",
            revision_count=revision_count,
            created_by="usr_test",
        )
        session.add(api)
        await session.flush()
        for n in range(revisions):
            session.add(
                ApiRevision(
                    api_id=api.id,
                    spec_digest=f"sha-{name}-{n}",
                    source_type="inline",
                    created_by="usr_test",
                )
            )
        await session.commit()


async def _counts(registry_db: DatabaseSession) -> dict[str, int]:
    async with registry_db.session() as session:
        rows = await session.execute(select(Api.name, Api.revision_count))
        return {str(row.name): int(row.revision_count) for row in rows.all()}


async def _run(registry_db: DatabaseSession) -> int:
    def _reconcile(sync_session: Session) -> int:
        return reconcile_migration.reconcile_revision_counts(sync_session.connection())

    async with registry_db.session() as session:
        updated = await session.run_sync(_reconcile)
        await session.commit()
    return updated


async def test_reconcile_repairs_drifted_counts_and_is_idempotent(
    registry_db: DatabaseSession, clean_registry: None
) -> None:
    await _seed_api(registry_db, name="inflated", revision_count=100, revisions=1)
    await _seed_api(registry_db, name="deflated", revision_count=1, revisions=2)
    await _seed_api(registry_db, name="empty", revision_count=3, revisions=0)
    await _seed_api(registry_db, name="exact", revision_count=2, revisions=2)

    assert await _run(registry_db) == 3
    assert await _counts(registry_db) == {"inflated": 1, "deflated": 2, "empty": 0, "exact": 2}

    assert await _run(registry_db) == 0
    assert await _counts(registry_db) == {"inflated": 1, "deflated": 2, "empty": 0, "exact": 2}
