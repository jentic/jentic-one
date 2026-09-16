"""Unit tests for the broker execution pipeline (interceptor chain)."""

from __future__ import annotations

import pytest

from jentic_one.broker.adapters.runners.base import RunnerRequest, RunnerResult
from jentic_one.broker.core.exceptions import ErrorOrigin
from jentic_one.broker.services.execution.pipeline import (
    BrokerExecutionPipeline,
    ExecutionContext,
    ExecutionOutcome,
    enrich_error_origin,
)
from jentic_one.shared.schemas import OperationInfo


class _FakeRunner:
    def __init__(self, status: int) -> None:
        self._status = status
        self.calls: list[RunnerRequest] = []

    async def run(self, request: RunnerRequest) -> RunnerResult:
        self.calls.append(request)
        return RunnerResult(
            status_code=self._status,
            body=b"{}",
            headers={"content-type": "application/json"},
            content_type="application/json",
            duration_ms=1,
        )


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        execution_id="exec_x",
        toolkit_id="tk",
        operation=OperationInfo(id="op"),
        api=None,
        trace_id="trace",
    )


def _outcome(status: int) -> ExecutionOutcome:
    return ExecutionOutcome(
        result=RunnerResult(
            status_code=status,
            body=b"",
            headers={},
            content_type=None,
            duration_ms=0,
        ),
        context=_ctx(),
    )


def test_enrich_marks_upstream_on_4xx() -> None:
    out = enrich_error_origin(_outcome(404))
    assert out.error_origin is ErrorOrigin.UPSTREAM


def test_enrich_marks_upstream_on_5xx() -> None:
    out = enrich_error_origin(_outcome(503))
    assert out.error_origin is ErrorOrigin.UPSTREAM


def test_enrich_leaves_2xx_unmarked() -> None:
    out = enrich_error_origin(_outcome(200))
    assert out.error_origin is None


def test_enrich_is_idempotent() -> None:
    first = enrich_error_origin(_outcome(500))
    second = enrich_error_origin(first)
    assert second.error_origin is ErrorOrigin.UPSTREAM


@pytest.mark.asyncio
async def test_pipeline_dispatches_runner_then_enriches() -> None:
    runner = _FakeRunner(502)
    pipeline = BrokerExecutionPipeline(runner)
    req = RunnerRequest(method="GET", url="https://x/y")
    outcome = await pipeline.execute(req, _ctx())
    assert runner.calls == [req]
    assert outcome.result.status_code == 502
    assert outcome.error_origin is ErrorOrigin.UPSTREAM


@pytest.mark.asyncio
async def test_pipeline_success_no_origin() -> None:
    pipeline = BrokerExecutionPipeline(_FakeRunner(200))
    outcome = await pipeline.execute(RunnerRequest(method="GET", url="https://x"), _ctx())
    assert outcome.error_origin is None


@pytest.mark.asyncio
async def test_pipeline_runs_custom_stages_in_order() -> None:
    order: list[str] = []

    def stage_a(outcome: ExecutionOutcome) -> ExecutionOutcome:
        order.append("a")
        return outcome

    def stage_b(outcome: ExecutionOutcome) -> ExecutionOutcome:
        order.append("b")
        return outcome

    pipeline = BrokerExecutionPipeline(_FakeRunner(200), post_response=[stage_a, stage_b])
    await pipeline.execute(RunnerRequest(method="GET", url="https://x"), _ctx())
    assert order == ["a", "b"]
