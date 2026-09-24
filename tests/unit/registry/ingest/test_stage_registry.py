"""Tests for the ingest pipeline stage-registration seam (register_pipeline_stage)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.registry.ingest.pipeline.ctx import PipelineContext
from jentic_one.registry.ingest.pipeline.pipeline import PipelineFactory
from jentic_one.registry.ingest.pipeline.stage_registry import (
    _REGISTERED_STAGES,
    PipelineStageSpec,
    register_pipeline_stage,
    registered_pipeline_stage_specs,
    registered_pipeline_stages,
)
from jentic_one.registry.ingest.stages.base import BasePipelineStage
from jentic_one.registry.ingest.stages.search_text import BuildSearchTextForOperationsStage


class _ExtensionStage(BasePipelineStage):
    name = "ExtensionStage"

    async def _run(self, ctx: PipelineContext) -> None:  # pragma: no cover - never run
        pass


class _OtherExtensionStage(BasePipelineStage):
    name = "OtherExtensionStage"

    async def _run(self, ctx: PipelineContext) -> None:  # pragma: no cover - never run
        pass


@pytest.fixture(autouse=True)
def _isolated_registry() -> Iterator[None]:
    """Snapshot/restore the process-global registry around every test."""
    before = dict(_REGISTERED_STAGES)
    _REGISTERED_STAGES.clear()
    try:
        yield
    finally:
        _REGISTERED_STAGES.clear()
        _REGISTERED_STAGES.update(before)


def _openapi_spec() -> IngestSpecification:
    return IngestSpecification(
        spec_type=SpecType.OPENAPI,
        api_identifier=ApiIdentifier(vendor="acme", name="pets", version="1.0.0"),
        content={"openapi": "3.1.0"},
    )


def test_registered_stage_appends_after_builtins_and_search_text() -> None:
    register_pipeline_stage(PipelineStageSpec(name="ext", factory=_ExtensionStage))

    pipeline = PipelineFactory.from_specification(_openapi_spec(), include_search_text=True)

    assert isinstance(pipeline.stages[-1], _ExtensionStage)
    assert isinstance(pipeline.stages[-2], BuildSearchTextForOperationsStage)


def test_registration_order_is_execution_order() -> None:
    register_pipeline_stage(PipelineStageSpec(name="first", factory=_ExtensionStage))
    register_pipeline_stage(PipelineStageSpec(name="second", factory=_OtherExtensionStage))

    pipeline = PipelineFactory.from_specification(_openapi_spec())

    assert isinstance(pipeline.stages[-2], _ExtensionStage)
    assert isinstance(pipeline.stages[-1], _OtherExtensionStage)


def test_identical_reregistration_is_idempotent() -> None:
    spec = PipelineStageSpec(name="ext", factory=_ExtensionStage)
    register_pipeline_stage(spec)
    # Same frozen spec again (double import) — no-op, not an error.
    register_pipeline_stage(PipelineStageSpec(name="ext", factory=_ExtensionStage))

    assert registered_pipeline_stage_specs() == (spec,)


def test_conflicting_name_is_rejected() -> None:
    register_pipeline_stage(PipelineStageSpec(name="ext", factory=_ExtensionStage))
    with pytest.raises(ValueError, match="already registered"):
        register_pipeline_stage(PipelineStageSpec(name="ext", factory=_OtherExtensionStage))


def test_empty_spec_types_filter_is_rejected() -> None:
    with pytest.raises(ValueError, match="empty spec_types"):
        register_pipeline_stage(
            PipelineStageSpec(name="never", factory=_ExtensionStage, spec_types=frozenset())
        )


def test_spec_type_filter_and_none_default() -> None:
    # Only one SpecType exists today, so prove the filter's two live branches:
    # an explicit matching set and the None default (matches everything).
    register_pipeline_stage(
        PipelineStageSpec(
            name="openapi_only", factory=_ExtensionStage, spec_types=frozenset({SpecType.OPENAPI})
        )
    )
    register_pipeline_stage(PipelineStageSpec(name="always", factory=_OtherExtensionStage))

    stages = registered_pipeline_stages(SpecType.OPENAPI)

    assert [type(s) for s in stages] == [_ExtensionStage, _OtherExtensionStage]


def test_fresh_instance_per_pipeline_build() -> None:
    register_pipeline_stage(PipelineStageSpec(name="ext", factory=_ExtensionStage))

    first = PipelineFactory.from_specification(_openapi_spec()).stages[-1]
    second = PipelineFactory.from_specification(_openapi_spec()).stages[-1]

    assert first is not second


def test_registered_specs_snapshot_is_a_copy() -> None:
    spec = PipelineStageSpec(name="ext", factory=_ExtensionStage)
    register_pipeline_stage(spec)

    snapshot = registered_pipeline_stage_specs()

    assert snapshot == (spec,)
    assert isinstance(snapshot, tuple)  # immutable view, not the live registry


def test_unregistered_pipeline_shape_is_unchanged() -> None:
    # Full stage-type sequence, not just length: with nothing registered the
    # factory output is identical to the hardcoded built-in pipeline.
    with_search = PipelineFactory.from_specification(_openapi_spec(), include_search_text=True)
    without = PipelineFactory.from_specification(_openapi_spec())

    with_search_types = [type(s) for s in with_search.stages]
    without_types = [type(s) for s in without.stages]

    assert with_search_types == [*without_types, BuildSearchTextForOperationsStage]
    assert registered_pipeline_stage_specs() == ()


def test_pipeline_context_carries_optional_config() -> None:
    sentinel = object()
    ctx = PipelineContext(
        session=None,
        specification=_openapi_spec(),
        created_by="usr_test",
        config=sentinel,  # type: ignore[arg-type]
    )
    assert ctx.config is sentinel

    default = PipelineContext(session=None, specification=_openapi_spec(), created_by="usr_test")
    assert default.config is None
