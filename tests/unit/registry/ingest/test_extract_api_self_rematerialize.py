"""Unit tests for CreateRevisionStage._is_self_rematerialize (D1 superseded-capture guard).

These pin the fail-closed behaviour that decides whether an overlay ingest re-captures the
current revision as the new overlay's ``superseded_revision_id``:

- A re-materialize of the *same* overlay must NOT re-capture (keep the clean base) → True.
- A stacked confirm of a *different* overlay must capture → False.
- A missing / malformed ``overlay_id`` must fail *closed* (return False → capture), because a
  wrong *skip* is the only dangerous direction (it would strip a rollback target).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.registry.ingest.pipeline.ctx import PipelineContext
from jentic_one.registry.ingest.stages.extract_api import CreateRevisionStage


def _ctx() -> PipelineContext:
    spec = IngestSpecification(
        spec_type=SpecType.OPENAPI,
        api_identifier=ApiIdentifier(vendor="acme", name="pets", version="1.0.0"),
        content={"openapi": "3.1.0"},
    )
    # session is unused for the short-circuit cases; the match cases patch the repo.
    return PipelineContext(session=object(), specification=spec, created_by="usr_test")


@pytest.mark.asyncio
async def test_none_overlay_id_fails_closed() -> None:
    # A non-overlay ingest (no overlay_id) must never skip capture.
    result = await CreateRevisionStage._is_self_rematerialize(
        _ctx(), uuid.uuid4(), uuid.uuid4(), None
    )
    assert result is False


@pytest.mark.asyncio
async def test_malformed_overlay_id_fails_closed() -> None:
    # A malformed id (missing the ovr_ prefix) must fail closed WITHOUT a DB lookup.
    with patch(
        "jentic_one.registry.ingest.stages.extract_api.OverlayRepository."
        "get_live_confirmed_for_revision",
        new_callable=AsyncMock,
    ) as get_owner:
        result = await CreateRevisionStage._is_self_rematerialize(
            _ctx(), uuid.uuid4(), uuid.uuid4(), "not-an-overlay-id"
        )
    assert result is False
    get_owner.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_overlay_is_self_rematerialize() -> None:
    api_id = uuid.uuid4()
    rev_id = uuid.uuid4()
    owner = type("O", (), {"id": "ovr_self"})()
    with patch(
        "jentic_one.registry.ingest.stages.extract_api.OverlayRepository."
        "get_live_confirmed_for_revision",
        new_callable=AsyncMock,
        return_value=owner,
    ):
        result = await CreateRevisionStage._is_self_rematerialize(
            _ctx(), api_id, rev_id, "ovr_self"
        )
    assert result is True


@pytest.mark.asyncio
async def test_different_overlay_captures_prior() -> None:
    # Overlay B stacked over overlay A's live output: owner is A, so B (this job) must NOT be
    # treated as a self-rematerialize — it must capture A's revision as its superseded target.
    owner_a = type("O", (), {"id": "ovr_A"})()
    with patch(
        "jentic_one.registry.ingest.stages.extract_api.OverlayRepository."
        "get_live_confirmed_for_revision",
        new_callable=AsyncMock,
        return_value=owner_a,
    ):
        result = await CreateRevisionStage._is_self_rematerialize(
            _ctx(), uuid.uuid4(), uuid.uuid4(), "ovr_B"
        )
    assert result is False


@pytest.mark.asyncio
async def test_no_live_owner_captures() -> None:
    # No live confirmed overlay backs the current revision (e.g. a plain imported revision):
    # not a self-rematerialize, so capture proceeds.
    with patch(
        "jentic_one.registry.ingest.stages.extract_api.OverlayRepository."
        "get_live_confirmed_for_revision",
        new_callable=AsyncMock,
        return_value=None,
    ):
        result = await CreateRevisionStage._is_self_rematerialize(
            _ctx(), uuid.uuid4(), uuid.uuid4(), "ovr_orphan"
        )
    assert result is False
