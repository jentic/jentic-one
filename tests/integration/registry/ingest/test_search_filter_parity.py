"""Filter parity: every registered strategy applies the shared filter contract.

A composite strategy fusing several children is only correct if each child
admits exactly the operations the shared pin/active-state/api-filter predicate
(``jentic_one.registry.repos.search.filters``) admits. This test seeds revisions
in every relevant state through the real ``Ingestor`` and runs **every**
strategy registered for the active dialect against the same inputs, asserting
each returns exactly the revisions the contract allows.

Runs on both backends (Postgres and ``JENTIC_TEST_BACKEND=sqlite``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest

from jentic_one.registry.core.schema.api_revisions import ApiRevision
from jentic_one.registry.ingest.ingestor import Ingestor
from jentic_one.registry.ingest.models import ApiIdentifier, IngestSpecification, SpecType
from jentic_one.registry.repos.revision_repo import ApiRevisionRepository
from jentic_one.registry.repos.search import (
    ACTIVE_REVISION_STATES,
    SearchStrategy,
    resolve_strategy_by_name,
)
from jentic_one.registry.repos.search.registry import _STRATEGIES
from jentic_one.shared.config import SearchConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models import ApiRevisionState

pytestmark = pytest.mark.integration

_QUERY = "paritymark"


@dataclass(frozen=True)
class _Seeded:
    """One revision per state, keyed by name: ``imported``, ``draft``, ``archived``."""

    api_ids: dict[str, uuid.UUID]
    revision_ids: dict[str, uuid.UUID]


def _spec(*, vendor: str, origin: str | None) -> IngestSpecification:
    return IngestSpecification(
        api_identifier=ApiIdentifier(
            vendor=vendor, name="api", version="1.0.0", filename="spec.yaml"
        ),
        spec_type=SpecType.OPENAPI,
        content={
            "openapi": "3.1.0",
            "info": {"title": "API", "version": "1.0.0"},
            "paths": {
                "/things": {
                    "get": {
                        "operationId": f"list_{vendor.replace('-', '_')}",
                        "summary": f"List {_QUERY} things",
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        },
        sha=f"sha-{vendor}",
        origin=origin,
    )


@pytest.fixture()
async def seeded(ingest_context: Context, clean_registry: None) -> _Seeded:
    """An IMPORTED, a DRAFT, and an ARCHIVED revision, each on its own API."""
    ingest_context.config.search = SearchConfig(enabled=True, search_enabled=True)
    ingestor = Ingestor(ingest_context)
    results = {
        "imported": await ingestor.ingest(
            _spec(vendor="parity-imported", origin="catalog"), created_by="usr_test"
        ),
        "draft": await ingestor.ingest(
            _spec(vendor="parity-draft", origin=None), created_by="usr_test"
        ),
        "archived": await ingestor.ingest(
            _spec(vendor="parity-archived", origin="catalog"), created_by="usr_test"
        ),
    }
    async with ingest_context.registry_db.transaction() as session:
        await ApiRevisionRepository.set_state(
            session, results["archived"].revision_id, ApiRevisionState.ARCHIVED
        )

    api_ids: dict[str, uuid.UUID] = {}
    async with ingest_context.registry_db.session() as session:
        for key, result in results.items():
            revision = await session.get(ApiRevision, result.revision_id)
            assert revision is not None
            api_ids[key] = revision.api_id

    # Preconditions the cases below rely on.
    assert results["imported"].state in ACTIVE_REVISION_STATES
    assert results["draft"].state == ApiRevisionState.DRAFT
    assert ApiRevisionState.DRAFT not in ACTIVE_REVISION_STATES
    assert ApiRevisionState.ARCHIVED not in ACTIVE_REVISION_STATES
    return _Seeded(
        api_ids=api_ids,
        revision_ids={key: result.revision_id for key, result in results.items()},
    )


def _registered_names(ctx: Context) -> list[str]:
    dialect = ctx.registry_db.backend.dialect_name
    return sorted(name for (d, name) in _STRATEGIES if d == dialect)


async def _hit_revisions(
    ctx: Context,
    strategy: SearchStrategy,
    *,
    api_filters: list[uuid.UUID] | None = None,
    revision_pins: dict[uuid.UUID, uuid.UUID] | None = None,
) -> set[uuid.UUID]:
    async with ctx.registry_db.session() as session:
        hits = await strategy.search_operations(
            session,
            query=_QUERY,
            api_filters=api_filters,
            revision_pins=revision_pins,
            limit=50,
        )
    return {hit.revision_id for hit in hits}


async def test_every_registered_strategy_applies_shared_filter_contract(
    ingest_context: Context, seeded: _Seeded
) -> None:
    names = _registered_names(ingest_context)
    assert names, "no search strategy registered for the active dialect"
    api, rev = seeded.api_ids, seeded.revision_ids

    # (description, api_filters, revision_pins, expected revisions)
    cases: list[
        tuple[str, list[uuid.UUID] | None, dict[uuid.UUID, uuid.UUID] | None, set[uuid.UUID]]
    ] = [
        ("unpinned: active states only", None, None, {rev["imported"]}),
        (
            "a pin surfaces its revision regardless of state",
            None,
            {api["draft"]: rev["draft"]},
            {rev["imported"], rev["draft"]},
        ),
        (
            "a pin to an archived revision surfaces it",
            None,
            {api["archived"]: rev["archived"]},
            {rev["imported"], rev["archived"]},
        ),
        (
            "a pinned API contributes only its pinned revision",
            None,
            {api["imported"]: uuid.uuid4()},
            set(),
        ),
        ("api filter restricts to the listed APIs", [api["draft"]], None, set()),
        ("api filter keeps active revisions", [api["imported"]], None, {rev["imported"]}),
        (
            "api filter intersects with pins",
            [api["draft"]],
            {api["draft"]: rev["draft"]},
            {rev["draft"]},
        ),
    ]

    failures: list[str] = []
    for name in names:
        strategy = resolve_strategy_by_name(ingest_context.registry_db.backend.dialect_name, name)
        for description, api_filters, revision_pins, expected in cases:
            got = await _hit_revisions(
                ingest_context, strategy, api_filters=api_filters, revision_pins=revision_pins
            )
            if got != expected:
                failures.append(f"{name}: {description}: expected {expected}, got {got}")

    assert not failures, "strategies diverge from the shared filter contract:\n" + "\n".join(
        failures
    )
