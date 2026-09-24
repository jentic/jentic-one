"""Extension seam: registered ingest pipeline stages.

Overlays append extra stages to the built-in ingest pipeline without editing
it — e.g. an embed-at-import stage registered by a downstream package. Mirrors
the search-strategy registry (``jentic_one.registry.repos.search.registry``):
process-global, populated at import time, with the same conflict-guard posture
as the config and telemetry seams (idempotent for an identical
re-registration, loud on a conflicting one).

Contract:

- Registered stages run **after every built-in stage** (including the
  search-text stage when enabled), in **registration order**. They therefore
  see the full built-in context bag (``operation_ids``, ``revision_id``, …)
  and run inside the same ingest transaction — a failing registered stage
  rolls back the whole ingest, exactly like a built-in one.
- When several packages register stages, cross-package order is their import
  order; register all your stages from one place if relative order matters.
- ``factory`` must return a **fresh** :class:`BasePipelineStage` per call
  (pipelines are built per ingest; stages may carry per-run state) and must
  not raise — a raising factory fails pipeline *construction*, bypassing the
  per-stage error taxonomy.
- The stage class's ``name`` ClassVar is the key logs, spans, and the
  ``stages_total``/``stage_duration`` metrics are labelled with — keep it as
  unique as the registration name.
- Feature gating is the stage's own job: read your registered config section
  from ``ctx.config`` (see :class:`PipelineContext`) and return early when
  disabled. The factory seam stays config-blind on purpose — it keeps
  registration a pure import-time side effect.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from jentic_one.registry.ingest.models import SpecType
from jentic_one.registry.ingest.stages.base import BasePipelineStage


@dataclass(frozen=True)
class PipelineStageSpec:
    """A registered extension stage: unique name, factory, spec-type filter."""

    #: Unique registration key; conflicting re-use is rejected loudly.
    name: str
    #: Zero-arg factory returning a fresh stage instance per pipeline build.
    factory: Callable[[], BasePipelineStage]
    #: Spec types the stage applies to. ``None`` (default) means every type.
    #: An empty set is rejected at registration (it could only mean "never
    #: runs", which is always a bug).
    spec_types: frozenset[SpecType] | None = None


_REGISTERED_STAGES: dict[str, PipelineStageSpec] = {}


def register_pipeline_stage(spec: PipelineStageSpec) -> None:
    """Register an extension stage. Call at import time, before ingest runs.

    Idempotent for an identical re-registration (same frozen spec — safe under
    double import); raises ``ValueError`` when the name is already bound to a
    *different* spec, so two extensions can't silently fight over one slot.
    Same posture as ``register_config`` / ``register_telemetry_event``.
    """
    if spec.spec_types is not None and not spec.spec_types:
        msg = f"Pipeline stage {spec.name!r} has an empty spec_types filter (would never run)"
        raise ValueError(msg)
    existing = _REGISTERED_STAGES.get(spec.name)
    if existing is not None and existing != spec:
        msg = f"Pipeline stage {spec.name!r} is already registered to a different spec"
        raise ValueError(msg)
    _REGISTERED_STAGES[spec.name] = spec


def registered_pipeline_stage_specs() -> tuple[PipelineStageSpec, ...]:
    """Snapshot of the registered specs, in registration order.

    Public introspection for integrators' own tests (mirrors the config seam's
    ``registered_config_models``) — don't reach into the private dict.
    """
    return tuple(_REGISTERED_STAGES.values())


def registered_pipeline_stages(spec_type: SpecType) -> list[BasePipelineStage]:
    """Fresh instances of every registered stage applicable to *spec_type*.

    Registration order is the execution order (dicts preserve insertion order).
    """
    return [
        spec.factory()
        for spec in _REGISTERED_STAGES.values()
        if spec.spec_types is None or spec_type in spec.spec_types
    ]
