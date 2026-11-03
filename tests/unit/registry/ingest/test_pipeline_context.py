"""Tests for PipelineContext typed data bag."""

from __future__ import annotations

import uuid

import pytest

from jentic_one.registry.ingest.errors import (
    MissingProducedKeyError,
    MissingRequiredKeysError,
    WrongTypeProducedError,
    WrongTypeRequiredError,
)
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.registry.ingest.pipeline.ctx import PipelineContext


@pytest.fixture
def spec() -> IngestSpecification:
    return IngestSpecification(
        spec_type=SpecType.OPENAPI,
        api_identifier=ApiIdentifier(vendor="acme", name="pets", version="1.0.0"),
        content={"openapi": "3.1.0"},
    )


@pytest.fixture
def ctx(spec: IngestSpecification) -> PipelineContext:
    return PipelineContext(session=None, specification=spec, created_by="usr_test")


def test_produce_require_round_trip(ctx: PipelineContext) -> None:
    test_id = uuid.uuid4()
    ctx.produce("api_id", test_id, uuid.UUID)
    result = ctx.require("api_id", uuid.UUID)
    assert result == test_id


def test_produce_type_mismatch_raises(ctx: PipelineContext) -> None:
    with pytest.raises(TypeError, match="expected UUID"):
        ctx.produce("api_id", "not-a-uuid", uuid.UUID)


def test_require_missing_key_raises(ctx: PipelineContext) -> None:
    with pytest.raises(MissingRequiredKeysError):
        ctx.require("nonexistent", uuid.UUID)


def test_require_wrong_type_raises(ctx: PipelineContext) -> None:
    ctx.produce("api_id", uuid.uuid4(), uuid.UUID)
    with pytest.raises(WrongTypeRequiredError):
        ctx.require("api_id", str)


def test_get_returns_value(ctx: PipelineContext) -> None:
    ctx.produce("count", 42, int)
    assert ctx.get("count") == 42


def test_get_returns_default_for_missing(ctx: PipelineContext) -> None:
    assert ctx.get("missing") is None
    assert ctx.get("missing", "fallback") == "fallback"


def test_ensure_requires_passes_when_all_present(ctx: PipelineContext) -> None:
    ctx.produce("api_id", uuid.uuid4(), uuid.UUID)
    ctx.produce("count", 5, int)
    ctx.ensure_requires({"api_id": uuid.UUID, "count": int}, stage=None)


def test_ensure_requires_raises_for_missing_keys(ctx: PipelineContext) -> None:
    with pytest.raises(MissingRequiredKeysError) as exc_info:
        ctx.ensure_requires({"api_id": uuid.UUID, "name": str}, stage=None)
    assert "api_id" in exc_info.value.missing_keys
    assert "name" in exc_info.value.missing_keys


def test_ensure_requires_raises_for_wrong_type(ctx: PipelineContext) -> None:
    ctx.produce("api_id", "not-uuid", str)
    with pytest.raises(WrongTypeRequiredError):
        ctx.ensure_requires({"api_id": uuid.UUID}, stage=None)


def test_ensure_produces_passes_when_all_produced(ctx: PipelineContext) -> None:
    ctx.produce("api_id", uuid.uuid4(), uuid.UUID)
    ctx.ensure_produces({"api_id": uuid.UUID}, stage=None)


def test_ensure_produces_raises_for_missing_key(ctx: PipelineContext) -> None:
    with pytest.raises(MissingProducedKeyError):
        ctx.ensure_produces({"api_id": uuid.UUID}, stage=None)


def test_ensure_produces_raises_for_wrong_type(ctx: PipelineContext) -> None:
    ctx.produce("ids", ["a", "b"], list)
    with pytest.raises(WrongTypeProducedError):
        ctx.ensure_produces({"ids": set}, stage=None)
