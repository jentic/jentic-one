"""Integration tests for CreateRevisionStage's duplicate-revision constraint mapping."""

from __future__ import annotations

import pytest
from sqlalchemy import update

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.ingest.errors import DuplicateRevisionError, IngestPipelineError
from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ApiRevisionSourceType, ApiRevisionState

pytestmark = pytest.mark.integration

_SPEC: dict = {  # type: ignore[type-arg]
    "openapi": "3.1.0",
    "info": {"title": "Race API", "version": "1.0.0"},
    "servers": [{"url": "https://api.race.example.com"}],
    "paths": {
        "/ping": {"get": {"operationId": "ping", "responses": {"200": {"description": "ok"}}}}
    },
}


def _spec(*, sha: str, origin: str | None) -> IngestSpecification:
    return IngestSpecification(
        spec_type=SpecType.OPENAPI,
        api_identifier=ApiIdentifier(vendor="race", name="race-api", version="1.0.0"),
        sha=sha,
        content=_SPEC,
        source_type=ApiRevisionSourceType.INLINE,
        source_filename="openapi.json",
        origin=origin,
    )


async def test_lost_one_active_insert_race_surfaces_as_duplicate_revision(
    ingest_context: Context,
    registry_db: DatabaseSession,
    clean_registry: None,
) -> None:
    """Pins #1436: an insert that loses the ``ix_api_revisions_one_active`` race
    (another active revision lands between the stage's pre-checks and its flush)
    surfaces as DuplicateRevisionError, not as a generic unexpected stage failure.

    The competing active revision is set up deterministically: an origin-less
    revision is marked PUBLISHED, which the catalog import's origin-scoped
    archive does not touch, so its ``create_imported`` flush collides on the
    one-active index exactly as a concurrent winner would.
    """
    first = await Ingestor(ingest_context).ingest(_spec(sha="sha-a", origin=None), created_by="usr")
    async with registry_db.session() as session:
        await session.execute(
            update(ApiRevision)
            .where(ApiRevision.id == first.revision_id)
            .values(state=ApiRevisionState.PUBLISHED)
        )
        await session.commit()

    with pytest.raises(IngestPipelineError) as exc_info:
        await Ingestor(ingest_context).ingest(
            _spec(sha="sha-b", origin="catalog"), created_by="usr"
        )

    assert exc_info.value.message == DuplicateRevisionError().message
    assert isinstance(exc_info.value.__cause__, DuplicateRevisionError)
