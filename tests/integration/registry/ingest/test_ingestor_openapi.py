"""End-to-end integration tests for the Ingestor service with OpenAPI specs."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import ClassVar

import pytest
import structlog
from sqlalchemy import select

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.core.schema.apis import Api
from jentic_one.registry.core.schema.operation_url_index import OperationURLIndex
from jentic_one.registry.core.schema.operations import Operation
from jentic_one.registry.core.schema.security_schemes import SecurityScheme
from jentic_one.registry.core.schema.servers import Server
from jentic_one.registry.core.schema.spec_files import SpecFile
from jentic_one.registry.ingest.exc import IngestPipelineError
from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.registry.ingest.pipeline.ctx import PipelineContext
from jentic_one.registry.ingest.pipeline.stage_registry import (
    _REGISTERED_STAGES,
    PipelineStageSpec,
    register_pipeline_stage,
)
from jentic_one.registry.ingest.stages.base import BasePipelineStage
from jentic_one.shared.context import Context
from jentic_one.shared.db.session import DatabaseSession
from jentic_one.shared.models import ApiRevisionSourceType

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
    # Document-level requirement — operations inherit it (issue #772).
    "security": [{"bearerAuth": []}],
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
        # Document-level security must be resolved onto every operation's
        # stored raw_operation (#772) — this is what tells downstream
        # consumers the operation is authenticated.
        for op in ops:
            assert op.raw_operation is not None
            assert op.raw_operation["security"] == [{"bearerAuth": []}]

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


async def test_ingest_warns_when_schemes_declared_but_nothing_resolves(
    ingest_context: Context,
    registry_db: DatabaseSession,
    clean_registry: None,
) -> None:
    """A spec with securitySchemes but no security requirement anywhere warns.

    This is the silent state behind #772: scheme definitions import fine, but
    no operation resolves an effective requirement, so authenticated calls go
    out without credentials. The import must flag it.
    """
    no_requirement_spec = {
        **SAMPLE_OPENAPI_SPEC,
        "info": {"title": "Pet Store Unsecured", "version": "1.0.0"},
    }
    del no_requirement_spec["security"]
    spec = IngestSpecification(
        spec_type=SpecType.OPENAPI,
        api_identifier=ApiIdentifier(vendor="petstore", name="pet-store-api", version="1.0.0"),
        sha="warncase123",
        content=no_requirement_spec,
        source_type=ApiRevisionSourceType.INLINE,
        source_url=None,
        source_filename="openapi.json",
        submitted_by="test-harness",
    )

    ingestor = Ingestor(ingest_context)
    with structlog.testing.capture_logs() as logs:
        await ingestor.ingest(spec, created_by="usr_test")

    warnings = [log for log in logs if log["event"] == "security_schemes_unused"]
    assert len(warnings) == 1
    assert warnings[0]["log_level"] == "warning"
    assert warnings[0]["scheme_names"] == ["bearerAuth"]
    assert warnings[0]["operation_count"] == 3
    # Operations import without a security key — absence is unambiguous.
    async with registry_db.session() as session:
        ops = (await session.execute(select(Operation))).unique().scalars().all()
        assert all(op.raw_operation is not None for op in ops)
        assert all("security" not in (op.raw_operation or {}) for op in ops)


async def test_ingest_does_not_warn_when_document_security_resolves(
    ingest_context: Context,
    registry_db: DatabaseSession,
    clean_registry: None,
) -> None:
    """The document-level requirement resolving onto operations is not a warning."""
    ingestor = Ingestor(ingest_context)
    with structlog.testing.capture_logs() as logs:
        await ingestor.ingest(_build_spec(), created_by="usr_test")

    assert not [log for log in logs if log["event"] == "security_schemes_unused"]


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


async def test_reimport_same_active_spec_raises_duplicate_not_hang(
    ingest_context: Context,
    registry_db: DatabaseSession,
    clean_registry: None,
) -> None:
    """Re-importing an identical spec whose revision is still ACTIVE surfaces a
    clean DuplicateRevisionError — it neither inserts a duplicate nor hangs.

    Regression: a catalog re-import re-fetches the identical spec (same digest).
    Inserting a second row with the same (api_id, spec_digest) violated the unique
    constraint; the failure retried to the worker's dead-letter, which the CLI
    poller didn't recognise as terminal — so `jentic catalog import` looked like
    it hung until its 2-minute timeout. Now a live revision with the same content
    is treated as a genuine conflict and raised legibly (the CLI treats a
    dead-letter/duplicate as terminal), instead of silently overwriting or hanging.
    """
    spec = _build_spec(sha="same_digest_xyz")
    spec.origin = "catalog"

    ingestor = Ingestor(ingest_context)
    await ingestor.ingest(spec, created_by="usr_test")

    # Second import of the identical, still-active spec must raise, not duplicate.
    with pytest.raises(IngestPipelineError, match="identical content"):
        await ingestor.ingest(_reimport_spec(), created_by="usr_test")

    async with registry_db.session() as session:
        apis = (await session.execute(select(Api))).unique().scalars().all()
        assert len(apis) == 1
        revisions = (await session.execute(select(ApiRevision))).unique().scalars().all()
        assert len(revisions) == 1, "no duplicate revision should be created on re-import"
        assert revisions[0].state == "imported"


def _reimport_spec() -> IngestSpecification:
    spec = _build_spec(sha="same_digest_xyz")
    spec.origin = "catalog"
    return spec


# ── register_pipeline_stage seam: registered stages run inside the ingest ────


@pytest.fixture()
def _isolated_stage_registry() -> Iterator[None]:
    """Snapshot/restore the process-global stage registry around a test."""
    before = dict(_REGISTERED_STAGES)
    _REGISTERED_STAGES.clear()
    try:
        yield
    finally:
        _REGISTERED_STAGES.clear()
        _REGISTERED_STAGES.update(before)


class _CaptureStage(BasePipelineStage):
    """Extension stage that records what it saw from the pipeline context."""

    name = "TestCaptureStage"
    _requires: ClassVar[dict[str, type]] = {"operation_ids": set, "revision_id": uuid.UUID}

    captured: ClassVar[dict[str, object]] = {}

    async def _run(self, ctx: PipelineContext) -> None:
        type(self).captured = {
            "operation_ids": ctx.require("operation_ids", set),
            "revision_id": ctx.require("revision_id", uuid.UUID),
            "config": ctx.config,
        }


class _FailingStage(BasePipelineStage):
    name = "TestFailingStage"

    async def _run(self, ctx: PipelineContext) -> None:
        msg = "extension stage exploded"
        raise RuntimeError(msg)


async def test_registered_stage_runs_inside_ingest(
    ingest_context: Context,
    registry_db: DatabaseSession,
    clean_registry: None,
    _isolated_stage_registry: None,
) -> None:
    """A registered extension stage runs last, sees the built-ins' context
    products, and receives the loaded AppConfig for self-gating."""
    _CaptureStage.captured = {}
    register_pipeline_stage(PipelineStageSpec(name="test.capture", factory=_CaptureStage))

    result = await Ingestor(ingest_context).ingest(_build_spec(), created_by="usr_test")

    assert _CaptureStage.captured, "registered stage never ran"
    assert _CaptureStage.captured["revision_id"] == result.revision_id
    assert len(_CaptureStage.captured["operation_ids"]) == result.operation_count  # type: ignore[arg-type]
    assert _CaptureStage.captured["config"] is ingest_context.config


async def test_failing_registered_stage_rolls_back_ingest(
    ingest_context: Context,
    registry_db: DatabaseSession,
    clean_registry: None,
    _isolated_stage_registry: None,
) -> None:
    """A failing extension stage surfaces as IngestPipelineError and rolls back
    everything the built-in stages persisted — exactly like a built-in failure."""
    register_pipeline_stage(PipelineStageSpec(name="test.failing", factory=_FailingStage))

    with pytest.raises(IngestPipelineError, match="extension stage exploded"):
        await Ingestor(ingest_context).ingest(_build_spec(), created_by="usr_test")

    async with registry_db.session() as session:
        apis = (await session.execute(select(Api))).unique().scalars().all()
        assert apis == [], "failed extension stage must roll back the whole ingest"
