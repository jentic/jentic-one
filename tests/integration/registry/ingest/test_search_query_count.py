"""Regression test for batched API identity hydration in SearchService."""

from __future__ import annotations

import pytest
from sqlalchemy import event

from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.registry.services.search_service import ApiRef, SearchService
from jentic_one.shared.config import SearchConfig
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession

pytestmark = pytest.mark.integration


def _spec(*, vendor: str, name: str, version: str, host: str, index: int) -> IngestSpecification:
    """A one-operation spec sharing the searchable marker 'batchsearchmarker'."""
    return IngestSpecification(
        api_identifier=ApiIdentifier(
            vendor=vendor,
            name=name,
            version=version,
            filename="spec.yaml",
        ),
        spec_type=SpecType.OPENAPI,
        content={
            "openapi": "3.1.0",
            "info": {"title": f"API {index}", "version": version},
            "servers": [{"url": f"https://{host}/v{index}"}],
            "paths": {
                f"/items-{index}": {
                    "get": {
                        "operationId": f"list_items_{index}",
                        "summary": f"List batchsearchmarker items {index}",
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        },
        sha=f"sha-batchsearch-{index}",
        origin="catalog",
    )


async def test_search_batches_api_identity_queries(
    ingest_context: Context,
    registry_db: DatabaseSession,
    clean_registry: None,
) -> None:
    """Searching across 5 distinct APIs issues at most one query against ``apis``."""
    ingest_context.config.search = SearchConfig(enabled=True, search_enabled=True)
    ingestor = Ingestor(ingest_context)

    expected_apis: dict[str, ApiRef] = {}
    for idx in range(1, 6):
        vendor = f"vendor-{idx}-example-com"
        name = f"service-{idx}"
        version = f"{idx}.0.0"
        host = f"api{idx}.example.com"
        await ingestor.ingest(
            _spec(vendor=vendor, name=name, version=version, host=host, index=idx),
            created_by="usr_test",
        )
        expected_apis[vendor] = ApiRef(
            vendor=vendor,
            name=name,
            version=version,
            host=host,
        )

    apis_statements: list[str] = []

    def _count(*args: object) -> None:
        statement = str(args[2])
        if "FROM apis" in statement:
            apis_statements.append(statement)

    sync_engine = registry_db.engine.sync_engine
    event.listen(sync_engine, "before_cursor_execute", _count)
    try:
        page = await SearchService(ingest_context).search(
            query="batchsearchmarker",
            limit=10,
        )
    finally:
        event.remove(sync_engine, "before_cursor_execute", _count)

    assert len(page.data) == 5
    assert {hit.api.vendor: hit.api for hit in page.data} == expected_apis
    assert len(apis_statements) <= 1, (
        f"Expected at most 1 'FROM apis' query, got {len(apis_statements)}"
    )
