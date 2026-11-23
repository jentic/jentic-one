"""Integration tests for the lexical search-text pipeline stage."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.shared.config import SearchConfig
from jentic_one.shared.context import Context

pytestmark = pytest.mark.integration

SAMPLE_SPEC: dict = {  # type: ignore[type-arg]
    "openapi": "3.1.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "paths": {
        "/items": {
            "get": {
                "operationId": "listItems",
                "summary": "List items",
                "responses": {"200": {"description": "OK"}},
            },
        },
        "/items/{id}": {
            "post": {
                "operationId": "createItem",
                "summary": "Create an item",
                "responses": {"201": {"description": "Created"}},
            },
        },
    },
}


@pytest.fixture()
def spec() -> IngestSpecification:
    return IngestSpecification(
        api_identifier=ApiIdentifier(
            vendor="test-vendor",
            name="test-api",
            version="1.0.0",
            filename="spec.yaml",
        ),
        spec_type=SpecType.OPENAPI,
        content=SAMPLE_SPEC,
        sha="abc123",
    )


async def test_search_text_populated_when_enabled(
    ingest_context: Context,
    spec: IngestSpecification,
    clean_registry: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ingest_context.config, "search", SearchConfig(enabled=True))

    ingestor = Ingestor(ingest_context)
    result = await ingestor.ingest(spec, created_by="usr_test")

    async with ingest_context.registry_db.session() as session:
        ops = (
            (
                await session.execute(
                    select(Operation).where(Operation.revision_id == result.revision_id)
                )
            )
            .unique()
            .scalars()
            .all()
        )
        assert len(ops) == 2
        for op in ops:
            assert op.search_text
            # Projection surfaces the operationId as the primary identifier.
            assert op.operation_id is not None
            assert op.operation_id in op.search_text


async def test_search_text_null_when_disabled(
    ingest_context: Context,
    spec: IngestSpecification,
    clean_registry: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ingest_context.config, "search", SearchConfig(enabled=False))

    ingestor = Ingestor(ingest_context)
    result = await ingestor.ingest(spec, created_by="usr_test")

    async with ingest_context.registry_db.session() as session:
        ops = (
            (
                await session.execute(
                    select(Operation).where(Operation.revision_id == result.revision_id)
                )
            )
            .unique()
            .scalars()
            .all()
        )
        assert len(ops) == 2
        for op in ops:
            assert op.search_text is None
