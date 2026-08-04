"""Extension seam: registered ingest pipeline stages.

Overlays append extra stages to the built-in ingest pipeline without editing
it — e.g. an enterprise embed-at-import stage for semantic search. Mirrors the
search-strategy registry (``jentic_one.registry.repos.search.registry``):
process-global, populated at import time, with the collision guards the config
and telemetry seams use.

Contract:

- Registered stages run **after every built-in stage** (including the
  search-text stage when enabled), in **registration order**. They therefore
  see the full built-in context bag (``operation_ids``, ``revision_id``, …)
  and run inside the same ingest transaction — a failing registered stage
  rolls back the whole ingest, exactly like a built-in one.
- ``factory`` must return a **fresh** :class:`BasePipelineStage` per call
  (pipelines are built per ingest; stages may carry per-run state).
- Feature gating is the stage's own job: read your registered config section
  from ``ctx.config`` (see :class:`PipelineContext`) and return early when
  disabled. The factory seam stays config-blind on purpose — it keeps
  registration a pure import-time side effect.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from jentic_one.registry.ingest.models import SpecType
from jentic_one.registry.ingest.stages.base import BasePipelineStage


@dataclass(frozen=True)
class PipelineStageSpec:
    """A registered extension stage: unique name, factory, spec-type filter."""

    #: Unique registration key; collisions are rejected loudly (typo safety).
    name: str
    #: Zero-arg factory returning a fresh stage instance per pipeline build.
    factory: Callable[[], BasePipelineStage]
    #: Spec types the stage applies to. ``None`` (default) means every type.
    spec_types: frozenset[SpecType] | None = field(default=None)


_REGISTERED_STAGES: dict[str, PipelineStageSpec] = {}


def register_pipeline_stage(spec: PipelineStageSpec) -> PipelineStageSpec:
    """Register an extension stage. Call at import time, before ingest runs.

    Raises ``ValueError`` on a duplicate name so two extensions (or a re-import
    bug) can't silently fight over one slot — same posture as
    ``register_config`` / ``register_telemetry_event``.
    """
    if spec.name in _REGISTERED_STAGES:
        msg = f"Pipeline stage {spec.name!r} is already registered"
        raise ValueError(msg)
    _REGISTERED_STAGES[spec.name] = spec
    return spec


def registered_pipeline_stages(spec_type: SpecType) -> list[BasePipelineStage]:
    """Fresh instances of every registered stage applicable to *spec_type*.

    Registration order is the execution order (dicts preserve insertion order).
    """
    return [
        spec.factory()
        for spec in _REGISTERED_STAGES.values()
        if spec.spec_types is None or spec_type in spec.spec_types
    ]
