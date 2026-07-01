"""Integration tests for the search revision-state filter.

Search must surface *active* revisions — both PUBLISHED and IMPORTED — so that
catalog imports (which land as IMPORTED, never auto-promoted) are findable
without a manual promote. DRAFT and ARCHIVED revisions must stay excluded.

Runs end-to-end through the real ``Ingestor`` (populates ``search_text`` and,
on SQLite, the ``operations_fts`` triggers) and the real ``SearchService``.
"""

from __future__ import annotations

import uuid

import pytest

from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.registry.repos.revision_repo import ApiRevisionRepository
from jentic_one.registry.services.search_service import SearchService
from jentic_one.shared.config import SearchConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models import ApiRevisionState

pytestmark = pytest.mark.integration


def _spec(*, vendor: str, marker: str, origin: str | None) -> IngestSpecification:
    """A one-operation spec whose summary embeds a unique searchable ``marker``."""
    return IngestSpecification(
        api_identifier=ApiIdentifier(
            vendor=vendor,
            name="api",
            version="1.0.0",
            filename="spec.yaml",
        ),
        spec_type=SpecType.OPENAPI,
        content={
            "openapi": "3.1.0",
            "info": {"title": "API", "version": "1.0.0"},
            "paths": {
                "/things": {
                    "get": {
                        "operationId": f"list_{marker}",
                        "summary": f"List {marker} things",
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        },
        sha=f"sha-{marker}",
        origin=origin,
    )


async def _search_hit_count(ctx: Context, query: str) -> int:
    """Return how many operations search returns for ``query``."""
    page = await SearchService(ctx).search(query=query, limit=50)
    return len(page.data)


async def test_search_returns_imported_revisions(
    ingest_context: Context, clean_registry: None
) -> None:
    """A catalog-style IMPORTED revision (origin set, never promoted) is searchable."""
    ingest_context.config.search = SearchConfig(enabled=True, search_enabled=True)
    ingestor = Ingestor(ingest_context)

    result = await ingestor.ingest(
        _spec(vendor="imported.example.com", marker="importedmarker", origin="catalog"),
        created_by="usr_test",
    )
    # Precondition: ingest really produced an IMPORTED revision, not PUBLISHED.
    assert result.state == ApiRevisionState.IMPORTED

    assert await _search_hit_count(ingest_context, "importedmarker") == 1


async def test_search_excludes_draft_revisions(
    ingest_context: Context, clean_registry: None
) -> None:
    """A DRAFT revision (uploaded, no origin) must NOT be searchable until promoted."""
    ingest_context.config.search = SearchConfig(enabled=True, search_enabled=True)
    ingestor = Ingestor(ingest_context)

    result = await ingestor.ingest(
        _spec(vendor="draft.example.com", marker="draftmarker", origin=None),
        created_by="usr_test",
    )
    assert result.state == ApiRevisionState.DRAFT

    assert await _search_hit_count(ingest_context, "draftmarker") == 0


async def test_search_excludes_archived_revisions(
    ingest_context: Context, clean_registry: None
) -> None:
    """Once an IMPORTED revision is archived, it drops out of search results."""
    ingest_context.config.search = SearchConfig(enabled=True, search_enabled=True)
    ingestor = Ingestor(ingest_context)

    result = await ingestor.ingest(
        _spec(vendor="archived.example.com", marker="archivedmarker", origin="catalog"),
        created_by="usr_test",
    )
    assert result.state == ApiRevisionState.IMPORTED

    # Sanity: findable while active.
    assert await _search_hit_count(ingest_context, "archivedmarker") == 1

    async with ingest_context.registry_db.transaction() as session:
        await ApiRevisionRepository.set_state(
            session,
            uuid.UUID(str(result.revision_id)),
            ApiRevisionState.ARCHIVED,
        )

    assert await _search_hit_count(ingest_context, "archivedmarker") == 0
