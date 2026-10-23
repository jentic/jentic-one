"""Integration tests: ``apis.revision_count`` tracks the revision rows an ingest leaves.

Pins #1980: re-importing content whose digest matches a replaceable (draft or
archived) revision replaces that revision, so the API's ``revision_count`` must
stay equal to the number of ``api_revisions`` rows instead of growing by one per
import.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ApiRevisionSourceType, ApiRevisionState

pytestmark = pytest.mark.integration

_IDENTIFIER = ApiIdentifier(vendor="count.example.com", name="count-api", version="1.0.0")


def _content(title: str) -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": title, "version": "1.0.0"},
        "servers": [{"url": "https://api.count.example.com"}],
        "paths": {
            "/ping": {"get": {"operationId": "ping", "responses": {"200": {"description": "ok"}}}}
        },
    }


def _spec(*, sha: str, origin: str | None = None) -> IngestSpecification:
    return IngestSpecification(
        spec_type=SpecType.OPENAPI,
        api_identifier=_IDENTIFIER,
        sha=sha,
        content=_content(f"Count API {sha}"),
        source_type=ApiRevisionSourceType.INLINE,
        source_filename="openapi.json",
        origin=origin,
    )


async def _counts(registry_db: DatabaseSession) -> tuple[int, int, set[uuid.UUID]]:
    """Return ``(apis.revision_count, actual revision rows, revision ids)``."""
    async with registry_db.session() as session:
        api = (
            await session.execute(
                select(Api).where(
                    Api.vendor == _IDENTIFIER.vendor,
                    Api.name == _IDENTIFIER.name,
                    Api.version == _IDENTIFIER.version,
                )
            )
        ).scalar_one()
        rows = (
            await session.execute(
                select(func.count()).select_from(ApiRevision).where(ApiRevision.api_id == api.id)
            )
        ).scalar_one()
        ids = set(
            (
                await session.execute(select(ApiRevision.id).where(ApiRevision.api_id == api.id))
            ).scalars()
        )
        return api.revision_count, rows, ids


async def test_identical_draft_reimport_keeps_revision_count_equal_to_rows(
    ingest_context: Context,
    registry_db: DatabaseSession,
    clean_registry: None,
) -> None:
    """Re-importing identical draft content replaces the draft without inflating the count."""
    ingestor = Ingestor(ingest_context)

    first = await ingestor.ingest(_spec(sha="sha-a"), created_by="usr_a")
    assert await _counts(registry_db) == (1, 1, {first.revision_id})

    second = await ingestor.ingest(_spec(sha="sha-a"), created_by="usr_b")
    third = await ingestor.ingest(_spec(sha="sha-a"), created_by="usr_b")
    assert await _counts(registry_db) == (1, 1, {third.revision_id})
    assert second.revision_id != third.revision_id

    other = await ingestor.ingest(_spec(sha="sha-b"), created_by="usr_a")
    assert await _counts(registry_db) == (2, 2, {third.revision_id, other.revision_id})

    again = await ingestor.ingest(_spec(sha="sha-a"), created_by="usr_a")
    assert await _counts(registry_db) == (2, 2, {other.revision_id, again.revision_id})


async def test_reimport_over_archived_revision_keeps_revision_count_equal_to_rows(
    ingest_context: Context,
    registry_db: DatabaseSession,
    clean_registry: None,
) -> None:
    """A catalog re-import that replaces an archived same-digest revision keeps counts exact."""
    ingestor = Ingestor(ingest_context)

    first = await ingestor.ingest(_spec(sha="sha-a", origin="catalog"), created_by="usr")
    second = await ingestor.ingest(_spec(sha="sha-b", origin="catalog"), created_by="usr")
    async with registry_db.session() as session:
        archived = await session.get(ApiRevision, first.revision_id)
        assert archived is not None
        assert archived.state == ApiRevisionState.ARCHIVED
    assert await _counts(registry_db) == (2, 2, {first.revision_id, second.revision_id})

    third = await ingestor.ingest(_spec(sha="sha-a", origin="catalog"), created_by="usr")
    assert await _counts(registry_db) == (2, 2, {second.revision_id, third.revision_id})
