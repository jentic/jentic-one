"""Tests for BasePipelineStage error mapping — what reaches job error records."""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from jentic_one.registry.ingest.errors import IngestStageError
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.registry.ingest.pipeline.ctx import PipelineContext
from jentic_one.registry.ingest.stages.base import BasePipelineStage

_RAW_DETAIL = 'duplicate key value violates unique constraint "uq_apis_vendor_name"'


class _DriverErrorStage(BasePipelineStage):
    name = "driver_error_stage"

    async def _run(self, ctx: PipelineContext) -> None:
        raise RuntimeError(_RAW_DETAIL)


class _DomainErrorStage(BasePipelineStage):
    name = "domain_error_stage"

    async def _run(self, ctx: PipelineContext) -> None:
        raise IngestStageError("spec content is empty")


def _make_ctx() -> PipelineContext:
    spec = IngestSpecification(
        spec_type=SpecType.OPENAPI,
        api_identifier=ApiIdentifier(vendor="acme", name="pets", version="1.0.0"),
        content={"openapi": "3.1.0", "info": {"title": "Pets", "version": "1.0.0"}},
    )
    return PipelineContext(session=None, specification=spec, created_by="usr_test")


async def test_unexpected_stage_error_message_is_generic() -> None:
    """Pins #1436: an unexpected exception maps to a generic stage error whose
    message never echoes the raw exception text, while the detail is still
    logged server-side."""
    with capture_logs() as logs, pytest.raises(IngestStageError) as exc_info:
        await _DriverErrorStage().run(_make_ctx())

    assert exc_info.value.message == "Stage 'driver_error_stage' failed unexpectedly"
    assert _RAW_DETAIL not in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, RuntimeError)

    logged = next(e for e in logs if e["event"] == "stage_unexpected_error")
    assert logged["stage"] == "driver_error_stage"
    assert logged["error"] == _RAW_DETAIL
    assert logged["error_type"] == "RuntimeError"


async def test_domain_stage_error_message_passes_through() -> None:
    """An IngestStageError raised by a stage is a deliberate, operator-facing
    message and propagates unchanged."""
    with pytest.raises(IngestStageError, match=r"^spec content is empty$"):
        await _DomainErrorStage().run(_make_ctx())
