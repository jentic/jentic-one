"""End-to-end integration tests for the Ingestor service with OpenAPI specs."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.security_schemes import SecurityScheme
from jentic_one.registry.core.schema.servers import Server
from jentic_one.registry.core.schema.spec_files import SpecFile
from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ApiRevisionSourceType
from jentic_one.shared.models.registry import ApiRevisionState

pytestmark = pytest.mark.integration

SAMPLE_OPENAPI_SPEC: dict = {  # type: ignore[type-arg]
    "openapi": "3.1.0",
    "info": {"title": "Pet Store", "version": "1.0.0"},
    "servers": [
        {
            "url": "https://api.petstore.example.com/v1",
            "description": "Production",
        }
    ],
    "paths": {
        "/pets": {
            "get": {
                "operationId": "listPets",
                "summary": "List all pets",
                "tags": ["pets"],
                "responses": {"200": {"description": "A list of pets"}},
            },
            "post": {
                "operationId": "createPet",
                "summary": "Create a pet",
                "tags": ["pets"],
                "responses": {"201": {"description": "Pet created"}},
            },
        },
        "/pets/{petId}": {
            "get": {
                "operationId": "getPet",
                "summary": "Get a pet by ID",
                "tags": ["pets"],
                "responses": {"200": {"description": "A pet"}},
            },
        },
    },
    "components": {
        "securitySchemes": {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
            }
        }
    },
}


def _build_spec(sha: str = "abc123def456") -> IngestSpecification:
    return IngestSpecification(
        spec_type=SpecType.OPENAPI,
        api_identifier=ApiIdentifier(
            vendor="petstore",
            name="pet-store-api",
            version="1.0.0",
        ),
        sha=sha,
        content=SAMPLE_OPENAPI_SPEC,
        source_type=ApiRevisionSourceType.INLINE,
        source_url=None,
        source_filename="openapi.json",
        submitted_by="test-harness",
    )


async def test_ingest_full_pipeline(
    ingest_context: Context,
    registry_db: DatabaseSession,
    clean_registry: None,
) -> None:
    """Full pipeline produces the expected rows for a realistic OpenAPI spec."""
    spec = _build_spec()
    ingestor = Ingestor(ingest_context)
    result = await ingestor.ingest(spec, created_by="usr_test")

    assert result.api_vendor == "petstore"
    assert result.api_name == "pet-store-api"
    assert result.api_version == "1.0.0"
    assert result.state == "draft"
    assert result.operation_count == 3

    async with registry_db.session() as session:
        apis = (await session.execute(select(Api))).unique().scalars().all()
        assert len(apis) == 1
        api = apis[0]
        assert api.vendor == "petstore"
        assert api.name == "pet-store-api"
        assert api.revision_count == 1
        assert api.operation_count == 3

        revisions = (await session.execute(select(ApiRevision))).unique().scalars().all()
        assert len(revisions) == 1
        rev = revisions[0]
        assert rev.state == "draft"
        assert rev.spec_digest == "abc123def456"
        assert rev.operation_count == 3
        assert api.current_revision_id is None

        ops = (await session.execute(select(Operation))).unique().scalars().all()
        assert len(ops) == 3

        servers = (await session.execute(select(Server))).unique().scalars().all()
        assert len(servers) >= 1

        schemes = (await session.execute(select(SecurityScheme))).unique().scalars().all()
        assert len(schemes) == 1
        assert schemes[0].name == "bearerAuth"

        spec_files = (await session.execute(select(SpecFile))).unique().scalars().all()
        assert len(spec_files) == 1
        assert spec_files[0].filename == "openapi.json"

        url_entries = (await session.execute(select(OperationURLIndex))).unique().scalars().all()
        assert len(url_entries) >= 3


async def test_ingest_idempotent_reingest(
    ingest_context: Context,
    registry_db: DatabaseSession,
    clean_registry: None,
) -> None:
    """Re-ingesting the same spec creates a second revision but the same Api."""
    spec1 = _build_spec(sha="digest_one")
    spec2 = _build_spec(sha="digest_two")

    ingestor = Ingestor(ingest_context)
    result1 = await ingestor.ingest(spec1, created_by="usr_test")
    result2 = await ingestor.ingest(spec2, created_by="usr_test")

    assert result1.revision_id != result2.revision_id

    async with registry_db.session() as session:
        apis = (await session.execute(select(Api))).unique().scalars().all()
        assert len(apis) == 1
        assert apis[0].revision_count == 2

        revisions = (await session.execute(select(ApiRevision))).unique().scalars().all()
        assert len(revisions) == 2


async def test_reimport_same_spec_is_idempotent(
    ingest_context: Context,
    registry_db: DatabaseSession,
    clean_registry: None,
) -> None:
    """Re-importing an unchanged spec reuses the revision instead of failing.

    Regression: a catalog re-import re-fetches the identical spec (same digest);
    with `origin` set the ingestor archived the old IMPORTED revision then tried
    to INSERT a new one with the same (api_id, spec_digest), violating the unique
    constraint. The failure retried to the worker's dead-letter, which the CLI
    poller didn't recognise as terminal — so `jentic catalog import` looked like
    it hung until its 2-minute timeout. The imported branch now reactivates the
    existing same-digest revision, so a re-import is a clean idempotent no-op.
    """
    spec = _build_spec(sha="same_digest_xyz")
    spec.origin = "catalog"

    ingestor = Ingestor(ingest_context)
    result1 = await ingestor.ingest(spec, created_by="usr_test")
    # Second import of the identical spec must NOT raise and must reuse the revision.
    result2 = await ingestor.ingest(_reimport_spec(), created_by="usr_test")

    assert result1.revision_id == result2.revision_id, "re-import should reuse the same revision"
    assert result2.state == "imported"

    async with registry_db.session() as session:
        apis = (await session.execute(select(Api))).unique().scalars().all()
        assert len(apis) == 1
        revisions = (await session.execute(select(ApiRevision))).unique().scalars().all()
        assert len(revisions) == 1, "no duplicate revision should be created on re-import"
        assert revisions[0].state == "imported"
        # Operations were rebuilt for the reused revision, not duplicated.
        ops = (await session.execute(select(Operation))).unique().scalars().all()
        assert len(ops) == 3


def _reimport_spec() -> IngestSpecification:
    spec = _build_spec(sha="same_digest_xyz")
    spec.origin = "catalog"
    return spec


async def test_reimport_does_not_demote_a_published_revision(
    ingest_context: Context,
    registry_db: DatabaseSession,
    clean_registry: None,
) -> None:
    """Re-importing a spec whose revision a human already PUBLISHED must not
    silently demote it back to IMPORTED.

    reactivate_imported reuses the same-digest revision on re-import; if it
    unconditionally set state=IMPORTED, a routine catalog re-import would revert
    an operator's publish decision. The guard preserves PUBLISHED.
    """
    spec = _build_spec(sha="same_digest_xyz")
    spec.origin = "catalog"
    ingestor = Ingestor(ingest_context)
    result1 = await ingestor.ingest(spec, created_by="usr_test")

    # Promote the imported revision to published (the human action).
    async with registry_db.session() as session:
        rev = await session.get(ApiRevision, result1.revision_id)
        assert rev is not None
        rev.state = ApiRevisionState.PUBLISHED
        await session.commit()

    # Re-import the identical spec.
    result2 = await ingestor.ingest(_reimport_spec(), created_by="usr_test")
    assert result1.revision_id == result2.revision_id

    async with registry_db.session() as session:
        revisions = (await session.execute(select(ApiRevision))).unique().scalars().all()
        assert len(revisions) == 1, "re-import must not create a second revision"
        assert revisions[0].state == ApiRevisionState.PUBLISHED, (
            "a published revision must survive re-import, not be demoted to imported"
        )
