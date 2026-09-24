"""Unit tests for CreateRevisionStage spec-digest derivation (#780).

Two invariants are pinned here:

- A production spec always carries a sha (``load_specification`` digests the
  bytes), so the stage asserts it — a sha-less spec fails loudly rather than
  silently persisting a NULL digest that would confuse the Flow-3 update sweep.
- A real sha flows through unchanged as the ``spec_digest``, and the
  duplicate/leftover-cleanup lookups run against it. The ``spec.sha or None``
  derivation also collapses an empty-string sha to NULL, so ``''`` (a *value*
  that would collide under ``uq_api_revisions_api_id_spec_digest``) can never
  reach the DB.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.registry.ingest.pipeline.ctx import PipelineContext
from jentic_one.registry.ingest.stages.extract_api import CreateRevisionStage

_STAGE_MODULE = "jentic_one.registry.ingest.stages.extract_api"


def _ctx(sha: str | None) -> PipelineContext:
    spec = IngestSpecification(
        spec_type=SpecType.OPENAPI,
        api_identifier=ApiIdentifier(vendor="acme", name="pets", version="1.0.0"),
        sha=sha,
        content={"openapi": "3.1.0"},
    )
    ctx = PipelineContext(session=object(), specification=spec, created_by="usr_test")
    ctx.produce("api_id", uuid.uuid4(), uuid.UUID)
    return ctx


def _draft_revision() -> object:
    """A minimal stand-in for an ApiRevision (only ``.id`` is read downstream)."""
    return type("R", (), {"id": uuid.uuid4()})()


@pytest.mark.asyncio
@pytest.mark.parametrize("sha", ["", None])
async def test_sha_less_spec_fails_loudly(sha: str | None) -> None:
    """A spec with no (or empty) sha must trip the invariant assert, not persist NULL.

    The ``spec.sha or None`` derivation would collapse both '' and None to NULL;
    the assert stops that from silently producing a NULL-digest revision (which
    would break the Flow-3 upstream-changed comparison). See #780.
    """
    with (
        patch(
            f"{_STAGE_MODULE}.ApiRevisionRepository.delete_replaceable_by_digest",
            new_callable=AsyncMock,
        ) as delete_replaceable,
        patch(
            f"{_STAGE_MODULE}.ApiRevisionRepository.create_draft",
            new_callable=AsyncMock,
            return_value=_draft_revision(),
        ) as create_draft,
        pytest.raises(AssertionError, match="without a sha"),
    ):
        await CreateRevisionStage()._run(_ctx(sha=sha))

    delete_replaceable.assert_not_awaited()
    create_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_sha_carrying_spec_runs_digest_lookups_and_passes_digest_through() -> None:
    with (
        patch(
            f"{_STAGE_MODULE}.ApiRevisionRepository.delete_replaceable_by_digest",
            new_callable=AsyncMock,
        ) as delete_replaceable,
        patch(
            f"{_STAGE_MODULE}.ApiRevisionRepository.get_by_digest",
            new_callable=AsyncMock,
            return_value=None,
        ) as get_by_digest,
        patch(
            f"{_STAGE_MODULE}.ApiRevisionRepository.create_draft",
            new_callable=AsyncMock,
            return_value=_draft_revision(),
        ) as create_draft,
    ):
        await CreateRevisionStage()._run(_ctx(sha="sha256:abc"))

    delete_replaceable.assert_awaited_once()
    assert delete_replaceable.await_args is not None
    _, _, deleted_digest = delete_replaceable.await_args.args
    assert deleted_digest == "sha256:abc"
    get_by_digest.assert_awaited_once()
    assert create_draft.await_args is not None
    assert create_draft.await_args.kwargs["spec_digest"] == "sha256:abc"
