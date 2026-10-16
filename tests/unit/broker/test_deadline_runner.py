"""Unit tests for the ``DeadlineRunner`` decorator + ``build_runner`` (§09 E4.1 / §11).

The deadline is the always-on whole-call wall-clock budget wrapping any
``UpstreamRunner``: a call that overruns raises ``DeadlineExceededError`` (504)
with a ``wait`` directive; a call within budget passes through verbatim; a
non-positive deadline disables the budget (unbounded passthrough).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import pytest

from jentic_one.broker.adapters.runners.base import (
    RunnerRequest,
    RunnerResult,
    StreamingResult,
)
from jentic_one.broker.adapters.runners.deadline import DeadlineRunner
from jentic_one.broker.core.exceptions import DeadlineExceededError, ErrorOrigin
from jentic_one.broker.services.execution.pipeline import build_runner


class _SlowRunner:
    """Inner runner that sleeps ``delay_s`` before returning a fixed result."""

    def __init__(self, *, delay_s: float = 0.0, status: int = 200) -> None:
        self.delay_s = delay_s
        self.status = status
        self.calls = 0

    async def run(self, request: RunnerRequest) -> RunnerResult:
        self.calls += 1
        await asyncio.sleep(self.delay_s)
        return RunnerResult(
            status_code=self.status, body=b"ok", headers={}, content_type=None, duration_ms=1
        )

    @contextlib.asynccontextmanager
    async def stream(self, request: RunnerRequest) -> AsyncIterator[StreamingResult]:
        self.calls += 1
        await asyncio.sleep(self.delay_s)
        yield StreamingResult(
            status_code=self.status,
            headers={},
            content_type=None,
            aiter=_empty_aiter(),
        )


async def _empty_aiter() -> AsyncIterator[bytes]:
    return
    yield  # pragma: no cover - makes this an async generator


def _request(url: str = "https://api.example.com/v1/x") -> RunnerRequest:
    return RunnerRequest(method="GET", url=url)


@pytest.mark.asyncio
async def test_within_deadline_passes_through() -> None:
    inner = _SlowRunner(delay_s=0.0, status=201)
    runner = DeadlineRunner(inner, deadline_s=1.0)

    result = await runner.run(_request())

    assert result.status_code == 201
    assert result.body == b"ok"
    assert inner.calls == 1


@pytest.mark.asyncio
async def test_exceeding_deadline_raises_504_with_directive() -> None:
    inner = _SlowRunner(delay_s=1.0)
    runner = DeadlineRunner(inner, deadline_s=0.01)

    with pytest.raises(DeadlineExceededError) as exc:
        await runner.run(_request())

    err = exc.value
    assert err.type == "deadline_exceeded"
    # The broker cut the call off and generated the 504 (#1675).
    assert err.origin is ErrorOrigin.BROKER
    assert err.directive is not None
    assert err.directive.strategy == "wait"
    assert err.directive.parameters["deadline_seconds"] == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_non_positive_deadline_disables_budget() -> None:
    inner = _SlowRunner(delay_s=0.05)
    runner = DeadlineRunner(inner, deadline_s=0.0)

    # Would blow a tiny budget, but the deadline is disabled, so it completes.
    result = await runner.run(_request())
    assert result.status_code == 200


@pytest.mark.asyncio
async def test_stream_within_deadline_yields_result() -> None:
    inner = _SlowRunner(delay_s=0.0)
    runner = DeadlineRunner(inner, deadline_s=1.0)

    async with runner.stream(_request()) as result:
        assert result.status_code == 200


@pytest.mark.asyncio
async def test_stream_exceeding_deadline_raises() -> None:
    inner = _SlowRunner(delay_s=1.0)
    runner = DeadlineRunner(inner, deadline_s=0.01)

    with pytest.raises(DeadlineExceededError):
        async with runner.stream(_request()):
            pass  # pragma: no cover - never reached


def test_build_runner_wraps_with_deadline() -> None:
    inner = _SlowRunner()
    composed = build_runner(inner, deadline_s=5.0)

    assert isinstance(composed, DeadlineRunner)


@pytest.mark.asyncio
async def test_build_runner_enforces_deadline() -> None:
    inner = _SlowRunner(delay_s=1.0)
    composed = build_runner(inner, deadline_s=0.01)

    with pytest.raises(DeadlineExceededError):
        await composed.run(_request())
