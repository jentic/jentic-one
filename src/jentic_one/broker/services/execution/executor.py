"""``PipelineExecutor`` — the broker-side adapter that unifies the async worker.

This is the broker half of the "one pipeline, two callers" seam (§00 / §05 /
§11 RN-0.3). The async worker (``shared/jobs/execution_handler.py``) cannot
import ``broker/`` (``tests/arch/test_module_boundaries.py``), so it depends on
the ``UpstreamExecutor`` protocol in ``shared/jobs/protocols.py``; this adapter
implements that protocol on the broker side and is dependency-injected into the
worker at startup.

It runs the upstream call through the **same** registry-selected runner the sync
router uses (``HttpRunner`` → optional ``CircuitBreakerRunner`` → retry/deadline
envelope, with the per-host bulkhead, response-size cap, and post-response
error-origin enrichment) and the **same** ``run_execution`` persistence step.
Before this adapter the worker issued raw ``httpx`` calls that bypassed the
circuit breaker, the bulkhead, and the pipeline's enrichment — a second
execution path that drifted from the sync one.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from jentic_one.broker.adapters.runners.base import UpstreamRunner
from jentic_one.broker.adapters.runners.registry import RunnerRegistry
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.services.execution.service import default_broker, run_execution
from jentic_one.shared.broker.broker import Broker
from jentic_one.shared.jobs.protocols import (
    UpstreamExecRequest,
    UpstreamExecResult,
    UpstreamExecutor,
)


class PipelineExecutor(UpstreamExecutor):
    """Adapts the shared ``Broker`` to the worker's protocol.

    Selects the runner for each call through the shared :class:`RunnerRegistry`
    (the same seam the sync router uses), so the circuit-breaker latch, per-host
    bulkhead, and connection pool are shared across sync + async — and a non-HTTP
    scheme routes to its runner once one is registered. Each call runs through
    ``run_execution`` against a :class:`Broker` built per request by
    ``broker_factory`` (default: :func:`default_broker`; a caller may inject its
    own),
    which dispatches the runner, folds the post-response stages, and persists the
    ``executions`` row. The worker keeps only its job-result + lifecycle-event writes.
    """

    def __init__(
        self,
        registry: RunnerRegistry,
        *,
        broker_factory: Callable[[UpstreamRunner], Broker] = default_broker,
    ) -> None:
        self._registry = registry
        self._broker_factory = broker_factory

    async def execute(self, request: UpstreamExecRequest, *, session: Any) -> UpstreamExecResult:
        ctx_req = _ctx_from_metadata(request)
        runner = self._registry.select(request.url)
        outcome = await run_execution(
            ctx_req,
            body=request.body,
            headers=request.headers,
            session=session,
            timeout=request.timeout_s,
            broker=self._broker_factory(runner),
            execution_id=request.metadata.get("execution_id"),
            actor_id=request.metadata["actor_id"],
            actor_type=request.metadata["actor_type"],
            origin=request.metadata.get("origin"),
        )
        result = outcome.result
        return UpstreamExecResult(
            status_code=result.status_code,
            body=result.body,
            content_type=result.content_type,
            duration_ms=result.duration_ms,
        )


def _ctx_from_metadata(request: UpstreamExecRequest) -> ExecuteRequestContext:
    """Rebuild the broker request context from the worker's opaque metadata."""
    meta = request.metadata
    return ExecuteRequestContext(
        upstream_url=request.url,
        method=request.method,
        trace_id=str(meta.get("trace_id", "unknown")),
        toolkit_id=meta.get("toolkit_id"),
        operation_id=meta.get("operation_id"),
        api_vendor=meta.get("api_vendor"),
        api_name=meta.get("api_name"),
        api_version=meta.get("api_version"),
        prefer=None,
        pinned_revisions=meta.get("pinned_revisions"),
    )


__all__ = ["PipelineExecutor"]
