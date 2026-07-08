"""Broker execution pipeline (composition root) — the ordered interceptor chain.

This is the single use-case both callers run: the **sync router** awaits it and
adapts the result to a FastAPI ``Response``; the **async worker** runs it and
persists the result. Neither re-implements the steps (00-overview "Composition
root — one pipeline, two callers").

The path is an **ordered chain of small stages**, not a god-method, so every
deferred capability slots in as a new stage / runner decorator at a defined
extension point without rewriting the core (plan.md "the execution path is an
ordered interceptor chain"):

- request/transport envelope → the ``UpstreamRunner`` (+ future
  idempotency/retry/circuit/deadline decorators wrapping it);
- post-response → an ordered list of pure ``PostResponseStage`` callables over
  the immutable ``ExecutionOutcome`` (error-origin/agent_directive enrichment,
  opt-in normalize_errors, opt-in rewrite_navigation).

PR-A1 ships the base ``HttpRunner`` and the error-origin enrichment stage; later
PRs add decorators/stages additively. An arch test asserts the router/worker hold
no inline envelope/post-processing logic.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Protocol, runtime_checkable

from jentic_one.broker.adapters.runners.base import (
    RunnerRequest,
    UpstreamRunner,
    capabilities_of,
)
from jentic_one.broker.adapters.runners.deadline import DeadlineRunner
from jentic_one.broker.adapters.runners.retry import RetryRunner
from jentic_one.shared.broker.execution import ErrorOrigin, ExecutionContext, ExecutionOutcome
from jentic_one.shared.broker.protocols import RunnerCapabilities
from jentic_one.shared.config import RetryConfig

__all__ = [
    "BrokerExecutionPipeline",
    "ExecutionContext",
    "ExecutionOutcome",
    "PostResponseStage",
    "build_runner",
    "enrich_error_origin",
]


@runtime_checkable
class PostResponseStage(Protocol):
    """A pure post-response stage: ``(outcome) -> outcome``.

    Stages are ordered after runner dispatch and individually testable without a
    live upstream. They must never mutate a 2xx success body.
    """

    def __call__(self, outcome: ExecutionOutcome) -> ExecutionOutcome: ...


def enrich_error_origin(outcome: ExecutionOutcome) -> ExecutionOutcome:
    """Tag a mirrored upstream 4xx/5xx with ``error_origin: upstream``.

    Header-only enrichment (the ``Jentic-Error-Origin`` header is added at
    response assembly); the body is left verbatim in default passthrough mode
    (B-002). The opt-in ``normalize_errors`` post-processor (§6b) is what rewrites
    the body — added in a later PR as another stage.
    """
    if outcome.error_origin is not None:
        return outcome
    if outcome.result.status_code >= 400:
        return replace(outcome, error_origin=ErrorOrigin.UPSTREAM)
    return outcome


def build_runner(
    base: UpstreamRunner,
    *,
    deadline_s: float,
    retry: RetryConfig | None = None,
    caps: RunnerCapabilities | None = None,
) -> UpstreamRunner:
    """Compose the always-on + capability-gated execution-envelope decorators.

    The §11 composition root (the "build_pipeline" sketch). ``base`` is the
    transport runner with any already-applied always-on layers (today: the
    optional :class:`CircuitBreakerRunner`, applied in the app lifespan). This
    adds the capability-gated **retry** loop (§09 E4.1) and then the **overall
    deadline** as the *outermost* always-on layer, so the wall-clock budget
    bounds the entire envelope — outside the retry loop, which is itself outside
    the breaker:

        DeadlineRunner( RetryRunner( CircuitBreakerRunner( HttpRunner ) ) )

    **Capability gating (§11 RN-0.3 "envelope split by capability").** The retry
    layer is added only when the runner *can* safely retry **and** the operator
    has it enabled — ``caps.supports_retries and retry.enabled``. ``caps`` is the
    capability profile of the underlying transport runner; when omitted it is
    derived from ``base`` (an undeclared runner reports no gated capabilities, so
    it is never wrapped in a layer it may not handle). Each retry attempt still
    flows through the breaker, and the loop honors the same ``deadline_s`` so a
    backoff sleep never overshoots the budget. The deadline is always-on:
    ``deadline_s <= 0`` leaves the decorator in place but disabled (unbounded),
    keeping the chain shape stable regardless of config.
    """
    capabilities = caps if caps is not None else capabilities_of(base)
    runner = base
    if retry is not None and retry.enabled and capabilities.supports_retries:
        runner = RetryRunner(
            runner,
            max_attempts=retry.max_attempts,
            base_backoff_s=retry.base_backoff_s,
            max_backoff_s=retry.max_backoff_s,
            retry_statuses=frozenset(retry.retry_statuses),
            deadline_s=deadline_s,
        )
    return DeadlineRunner(runner, deadline_s=deadline_s)


class BrokerExecutionPipeline:
    """Runs the transport envelope then the ordered post-response stages."""

    def __init__(
        self,
        runner: UpstreamRunner,
        *,
        post_response: Sequence[PostResponseStage] | None = None,
    ) -> None:
        self._runner = runner
        self._post_response: tuple[PostResponseStage, ...] = tuple(
            post_response if post_response is not None else (enrich_error_origin,)
        )

    async def execute(self, request: RunnerRequest, context: ExecutionContext) -> ExecutionOutcome:
        """Dispatch through the runner, then fold the post-response stages."""
        result = await self._runner.run(request)
        outcome = ExecutionOutcome(result=result, context=context)
        for stage in self._post_response:
            outcome = stage(outcome)
        return outcome
