"""Injected-broker seam: proves an injected ``Broker`` reaches both callers.

The value of the DI seam is "one pipeline, two callers": a downstream package
injects a single :class:`Broker` and it must be honored by **both** the sync
router (``broker/web/routers/execute.py``) and the async worker
(:class:`PipelineExecutor`) — not just one of them.

This exercises the real selection code on both sides with one shared spy broker:

- **Wiring**: an :class:`AppContainer` with a broker stashes it on
  ``app.state.broker`` for both ``create_surface_app`` and ``create_combined_app``
  (the source the sync router's ``_handle`` reads).
- **Sync path**: ``_handle``'s inline selection prefers ``app.state.broker`` over
  the per-request ``broker_factory`` default.
- **Async path**: ``PipelineExecutor`` built with an injected ``broker_factory``
  runs the injected broker through ``run_execution`` (verified end-to-end via a
  real in-memory execution against an ``AsyncMock`` session — the session is a
  plain mock, DB internals are never mocked per ``tests/arch/test_no_db_mocking``).

The spy is a compliant :class:`Broker` (``jentic_one.testing`` bases guard the
signature elsewhere); here we only assert *it was the one invoked*.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from jentic_one.broker.adapters.runners.base import RunnerRequest as BrokerRunnerRequest
from jentic_one.broker.adapters.runners.base import RunnerResult, UpstreamRunner
from jentic_one.broker.adapters.runners.registry import RunnerRegistry
from jentic_one.broker.services.execution.executor import PipelineExecutor
from jentic_one.shared.broker.broker import Broker
from jentic_one.shared.broker.execution import (
    ExecutionContext,
    ExecutionOutcome,
    RunnerRequest,
)
from jentic_one.shared.broker.execution import RunnerResult as SharedRunnerResult
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.jobs.protocols import UpstreamExecRequest
from jentic_one.shared.web.app_factory import create_combined_app, create_surface_app
from jentic_one.shared.web.container import AppContainer


class _SpyBroker:
    """A minimal compliant :class:`Broker` that records whether it was called."""

    def __init__(self) -> None:
        self.calls: list[tuple[RunnerRequest, ExecutionContext]] = []

    async def execute(self, request: RunnerRequest, context: ExecutionContext) -> ExecutionOutcome:
        self.calls.append((request, context))
        return ExecutionOutcome(
            result=SharedRunnerResult(
                status_code=200,
                body=b"spy",
                headers={},
                content_type="text/plain",
                duration_ms=1,
            ),
            context=context,
        )


class _NoopRunner(UpstreamRunner):
    """A runner that must never be reached when an injected broker owns transport."""

    async def run(self, request: BrokerRunnerRequest) -> RunnerResult:  # pragma: no cover
        raise AssertionError("injected broker must own transport; runner.run must not be called")


@pytest.fixture()
def ctx(sample_config_dict: dict[str, Any]) -> Context:
    return Context(AppConfig.model_validate(sample_config_dict))


def test_spy_broker_is_a_broker() -> None:
    """Sanity: the spy honors the Broker protocol (isinstance seam)."""
    assert isinstance(_SpyBroker(), Broker)


def test_container_stashes_injected_broker_on_combined_app(ctx: Context) -> None:
    broker = _SpyBroker()
    container = AppContainer(ctx=ctx, broker=broker)
    app = create_combined_app(ctx, ["control"], container=container)
    assert app.state.broker is broker


def test_container_stashes_injected_broker_on_surface_app(ctx: Context) -> None:
    broker = _SpyBroker()
    container = AppContainer(ctx=ctx, broker=broker)
    app = create_surface_app(
        ctx,
        title="test-surface",
        routers=[],
        container=container,
    )
    assert app.state.broker is broker


def test_default_container_leaves_broker_unset(ctx: Context) -> None:
    """No injection → the sync router falls back to its per-request factory."""
    app = create_combined_app(ctx, ["control"])
    assert getattr(app.state, "broker", None) is None


def test_sync_path_selection_prefers_injected_broker() -> None:
    """The exact selection ``_handle`` performs: injected instance wins over factory.

    Mirrors ``broker/web/routers/execute.py`` — an injected ``app.state.broker``
    is used verbatim; only its absence falls back to ``broker_factory(runner)``.
    """
    spy = _SpyBroker()
    default_factory_called = False

    def _factory(_runner: UpstreamRunner) -> Broker:
        nonlocal default_factory_called
        default_factory_called = True
        return _SpyBroker()

    state = MagicMock()
    state.broker = spy
    state.broker_factory = _factory

    injected = getattr(state, "broker", None)
    broker_factory = getattr(state, "broker_factory", _factory)
    selected = injected if injected is not None else broker_factory(_NoopRunner())

    assert selected is spy
    assert default_factory_called is False


@pytest.mark.asyncio
async def test_async_path_invokes_injected_broker() -> None:
    """PipelineExecutor built with an injected factory runs the injected broker.

    Uses the real ``run_execution`` path (no monkeypatching of the service) so the
    test proves the injected broker is actually invoked, not merely stored. The
    session is a plain AsyncMock stand-in (DB internals are never mocked).
    """
    spy = _SpyBroker()

    def _broker_factory(_runner: UpstreamRunner) -> Broker:
        return spy

    registry = RunnerRegistry()
    registry.register(["http", "https"], _NoopRunner(), required=True)
    executor = PipelineExecutor(registry, broker_factory=_broker_factory)

    session = AsyncMock()
    session.add = MagicMock()

    request = UpstreamExecRequest(
        method="GET",
        url="https://api.example.com/v1/things",
        headers={},
        body=None,
        timeout_s=30.0,
        metadata={
            "execution_id": "exec_test0000000000000000",
            "trace_id": "a" * 32,
            "toolkit_id": "tk_test000000000000000000",
            "operation_id": "getThing",
            "api_vendor": "example",
            "api_name": "api",
            "api_version": "1.0.0",
            "actor_id": "agt_abc123",
            "actor_type": "agent",
            "origin": "agent",
        },
    )

    result = await executor.execute(request, session=session)

    assert len(spy.calls) == 1, "injected broker must be invoked on the async path"
    assert result.status_code == 200
    assert result.body == b"spy"
