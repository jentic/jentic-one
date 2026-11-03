"""Tests for ValidateOpenAPISpec stage."""

from __future__ import annotations

import pytest

from jentic_one.registry.ingest.errors import IngestStageError
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.registry.ingest.pipeline.ctx import PipelineContext
from jentic_one.registry.ingest.stages.validation import ValidateOpenAPISpec


def _make_ctx(content: dict | None = None) -> PipelineContext:  # type: ignore[type-arg]
    spec = IngestSpecification(
        spec_type=SpecType.OPENAPI,
        api_identifier=ApiIdentifier(vendor="acme", name="pets", version="1.0.0"),
        content=content,
    )
    return PipelineContext(session=None, specification=spec, created_by="usr_test")


@pytest.mark.anyio
async def test_accepts_valid_openapi() -> None:
    ctx = _make_ctx({"openapi": "3.1.0", "info": {"title": "Pets", "version": "1.0.0"}})
    stage = ValidateOpenAPISpec()
    await stage.run(ctx)


@pytest.mark.anyio
async def test_rejects_empty_content() -> None:
    ctx = _make_ctx(None)
    stage = ValidateOpenAPISpec()
    with pytest.raises(IngestStageError, match="empty"):
        await stage.run(ctx)


@pytest.mark.anyio
async def test_rejects_missing_openapi_key() -> None:
    ctx = _make_ctx({"info": {"title": "Pets"}})
    stage = ValidateOpenAPISpec()
    with pytest.raises(IngestStageError, match="openapi"):
        await stage.run(ctx)


@pytest.mark.anyio
async def test_rejects_arazzo_spec() -> None:
    ctx = _make_ctx({"openapi": "3.1.0", "arazzo": "1.0.0"})
    stage = ValidateOpenAPISpec()
    with pytest.raises(IngestStageError, match=r"[Aa]razzo"):
        await stage.run(ctx)
