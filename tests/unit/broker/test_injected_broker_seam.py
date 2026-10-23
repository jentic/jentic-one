"""Injected-broker seam: proves an injected ``Broker`` reaches both callers.

The value of the DI seam is "one pipeline, two callers": a downstream package
injects a single :class:`Broker` and it must be honored by **both** the sync
router (``broker/web/routers/execute.py``) and the async worker
(:class:`PipelineExecutor`) — not just one of them.

This exercises the real selection code on both sides with one shared spy broker:

- **Wiring**: an :class:`AppContainer` with a broker stashes it on
  ``app.state.broker`` for both ``create_surface_app`` and ``create_combined_app``
  (the source the sync router's ``_handle`` reads).
- **Sync path**: ``_resolve_broker`` (the helper ``_handle`` uses) prefers
  ``app.state.broker`` over the per-request ``broker_factory`` default.
- **Async path**: ``PipelineExecutor`` built with an injected ``broker_factory``
  runs the injected broker through ``run_execution`` (verified end-to-end via a
  real in-memory execution against an ``AsyncMock`` session — the session is a
  plain mock, DB internals are never mocked per ``tests/arch/test_no_db_mocking``).

The spy is a compliant :class:`Broker` (``jentic_one.testing`` bases guard the
signature elsewhere); here we only assert *it was the one invoked*.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Response
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from jentic_one.broker.adapters.runners.base import RunnerRequest as BrokerRunnerRequest
from jentic_one.broker.adapters.runners.base import (
    RunnerResult,
    StreamingUpstreamRunner,
    UpstreamRunner,
)
from jentic_one.broker.adapters.runners.registry import RunnerRegistry
from jentic_one.broker.core.errors import CircuitOpenError
from jentic_one.broker.services.execution.executor import PipelineExecutor, _ctx_from_metadata
from jentic_one.broker.services.execution.service import _circuit_event_last_emitted
from jentic_one.broker.web.routers.execute import _resolve_broker
from jentic_one.shared.broker.broker import Broker
from jentic_one.shared.broker.execution import (
    ExecutionContext,
    ExecutionOutcome,
    RunnerRequest,
    StreamingOutcome,
)
from jentic_one.shared.broker.execution import RunnerResult as SharedRunnerResult
from jentic_one.shared.broker.schemas import ExecuteRequestContext
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.jobs.execution_handler import ExecutionHandler
from jentic_one.shared.jobs.protocols import ExecutionRecordNotPersistedError, UpstreamExecRequest
from jentic_one.shared.models.actors import Origin
from jentic_one.shared.models.events import ErrorSource, EventType
from jentic_one.shared.schemas import OperationInfo
from jentic_one.shared.web.app_factory import create_combined_app, create_surface_app
from jentic_one.shared.web.container import AppContainer
from tests.unit.fake_tx import FailingTransactions, SessionScope, post_upstream_write_failures


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

    async def execute_streaming(
        self,
        runner: StreamingUpstreamRunner,
        request: RunnerRequest,
        ctx_req: ExecuteRequestContext,
        execution_id: str,
        *,
        transfer_deadline_s: float,
        background_callback: Callable[[StreamingOutcome], Awaitable[None]] | None = None,
    ) -> Response:  # pragma: no cover - selection tests never invoke it
        return Response(content=b"spy-stream", status_code=200)


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
        enabled_apps={"broker"},
        container=container,
    )
    assert app.state.broker is broker


def test_default_container_leaves_broker_unset(ctx: Context) -> None:
    """No injection → the sync router falls back to its per-request factory."""
    app = create_combined_app(ctx, ["control"])
    assert getattr(app.state, "broker", None) is None


def test_sync_path_selection_prefers_injected_broker() -> None:
    """The real selection helper ``_handle`` uses: injected instance wins over factory.

    Calls ``broker/web/routers/execute.py::_resolve_broker`` directly (not a
    copy) so this test tracks the production logic — an injected
    ``app.state.broker`` is used verbatim; only its absence falls back to
    ``broker_factory(runner)``.
    """
    spy = _SpyBroker()
    default_factory_called = False

    def _factory(_runner: UpstreamRunner) -> Broker:
        nonlocal default_factory_called
        default_factory_called = True
        return _SpyBroker()

    request = MagicMock()
    request.app.state.broker = spy
    request.app.state.broker_factory = _factory

    assert _resolve_broker(request, _NoopRunner()) is spy
    assert default_factory_called is False


def test_sync_path_selection_falls_back_to_factory_when_unset() -> None:
    """With ``broker=None`` the per-request factory is used (fallback branch)."""
    made = _SpyBroker()
    factory_called = False

    def _factory(_runner: UpstreamRunner) -> Broker:
        nonlocal factory_called
        factory_called = True
        return made

    request = MagicMock()
    request.app.state.broker = None
    request.app.state.broker_factory = _factory

    assert _resolve_broker(request, _NoopRunner()) is made
    assert factory_called is True


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
            "operation": {"id": "getThing", "path": "/v1/things", "method": "GET"},
            "api_vendor": "example",
            "api_name": "api",
            "api_version": "1.0.0",
            "actor_id": "agt_abc123",
            "actor_type": "agent",
            "origin": "agent",
        },
    )

    result = await executor.execute(request, db=SessionScope(session))

    assert len(spy.calls) == 1, "injected broker must be invoked on the async path"
    assert result.status_code == 200
    assert result.body == b"spy"


class _RaisingBroker(_SpyBroker):
    async def execute(self, request: RunnerRequest, context: ExecutionContext) -> ExecutionOutcome:
        self.calls.append((request, context))
        raise CircuitOpenError(detail="circuit open")


def _exec_request() -> UpstreamExecRequest:
    return UpstreamExecRequest(
        method="POST",
        url="https://api.example.com/v1/things",
        headers={},
        body=None,
        timeout_s=30.0,
        metadata={
            "execution_id": "exec_test0000000000000001",
            "trace_id": "b" * 32,
            "actor_id": "agt_abc123",
            "actor_type": "agent",
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("error", post_upstream_write_failures())
@pytest.mark.parametrize(
    ("broker", "expected_status", "upstream_error"),
    [
        pytest.param(_SpyBroker(), 200, None, id="returned"),
        pytest.param(_RaisingBroker(), None, "circuit open", id="raised"),
    ],
)
async def test_async_path_record_write_failure_carries_the_upstream_result(
    broker: _SpyBroker, expected_status: int | None, upstream_error: str | None, error: Exception
) -> None:
    """Whatever the record write fails with once the broker call has finished,
    it is reported as an unpersisted record carrying the upstream's real result
    (or the broker's failure), so the worker neither reports a successful call
    as a pipeline failure nor requeues it."""
    broker.calls.clear()
    registry = RunnerRegistry()
    registry.register(["http", "https"], _NoopRunner(), required=True)
    executor = PipelineExecutor(registry, broker_factory=lambda _runner: broker)
    db = FailingTransactions(error)

    with pytest.raises(ExecutionRecordNotPersistedError) as caught:
        await executor.execute(_exec_request(), db=db)

    assert len(broker.calls) == 1
    assert db.opened == 1
    assert caught.value.__cause__ is error
    assert caught.value.upstream_error == upstream_error
    assert caught.value.runner_failed is False
    if expected_status is None:
        assert caught.value.result is None
    else:
        assert caught.value.result is not None
        assert caught.value.result.status_code == expected_status
        assert caught.value.result.body == b"spy"


class _CrashingBroker(_SpyBroker):
    async def execute(self, request: RunnerRequest, context: ExecutionContext) -> ExecutionOutcome:
        self.calls.append((request, context))
        raise RuntimeError("runner crashed")


@pytest.mark.asyncio
async def test_async_path_non_broker_error_after_dispatch_is_not_retryable() -> None:
    """A broker call that dies with a non-``BrokerError`` may already have sent
    the request and leaves no record, so it is reported as an unpersisted
    record (never requeued), naming only the error type."""
    broker = _CrashingBroker()
    registry = RunnerRegistry()
    registry.register(["http", "https"], _NoopRunner(), required=True)
    executor = PipelineExecutor(registry, broker_factory=lambda _runner: broker)
    db = SessionScope(AsyncMock())

    with pytest.raises(ExecutionRecordNotPersistedError) as caught:
        await executor.execute(_exec_request(), db=db)

    assert len(broker.calls) == 1
    assert caught.value.result is None
    assert caught.value.upstream_error == "RuntimeError"
    assert caught.value.runner_failed is True
    assert isinstance(caught.value.__cause__, RuntimeError)


@pytest.mark.asyncio
async def test_async_path_broker_error_with_committed_record_propagates() -> None:
    """A ``BrokerError`` whose failed record committed propagates unchanged, so
    the worker reports it as the pipeline failure it is."""
    broker = _RaisingBroker()
    registry = RunnerRegistry()
    registry.register(["http", "https"], _NoopRunner(), required=True)
    executor = PipelineExecutor(registry, broker_factory=lambda _runner: broker)
    session = AsyncMock()
    session.add = MagicMock()
    db = SessionScope(session)

    with pytest.raises(CircuitOpenError):
        await executor.execute(_exec_request(), db=db)

    assert db.opened == 1


@pytest.mark.asyncio
async def test_async_path_failure_before_dispatch_propagates_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An error raised before the broker is called propagates as itself: nothing
    was sent, so the worker may retry the job."""
    broker = _SpyBroker()
    registry = RunnerRegistry()
    registry.register(["http", "https"], _NoopRunner(), required=True)
    executor = PipelineExecutor(registry, broker_factory=lambda _runner: broker)
    error = PoolTimeoutError("QueuePool limit of size 5 overflow 10 reached")

    async def _fails_before_dispatch(*_args: Any, **_kwargs: Any) -> ExecutionOutcome:
        raise error

    monkeypatch.setattr(
        "jentic_one.broker.services.execution.executor.run_execution", _fails_before_dispatch
    )

    with pytest.raises(PoolTimeoutError) as caught:
        await executor.execute(_exec_request(), db=SessionScope(AsyncMock()))

    assert caught.value is error
    assert broker.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("error", post_upstream_write_failures())
async def test_worker_handler_completes_when_every_write_after_the_call_fails(
    error: Exception,
) -> None:
    """The real handler over the real ``PipelineExecutor``: when both the record
    and the lifecycle writes fail after a 2xx, the job completes with the 2xx
    and nothing raises (a raise would requeue and re-send the request)."""
    broker = _SpyBroker()
    registry = RunnerRegistry()
    registry.register(["http", "https"], _NoopRunner(), required=True)
    handler = ExecutionHandler(
        executor=PipelineExecutor(registry, broker_factory=lambda _runner: broker)
    )
    db = FailingTransactions(error)

    result = await handler.execute(
        "job-all-writes-fail",
        db,
        payload={
            "execution_id": "exec_test0000000000000002",
            "upstream_url": "https://api.example.com/v1/things",
            "method": "POST",
            "trace_id": "c" * 32,
        },
        created_by="agt_abc123",
        actor_type="agent",
    )

    assert len(broker.calls) == 1
    assert db.opened == 2
    assert result.body["status"] == "completed"
    assert result.body["http_status"] == 200
    assert result.body["body_b64"]


def test_ctx_from_metadata_rebuilds_credential_attribution() -> None:
    """The worker's opaque metadata carries credential attribution (#740) into
    the rebuilt request context, so the pipeline persists the same
    credential_id/name on async execution records as the sync router does —
    and omitting it leaves both None ("no credential used")."""
    base_metadata: dict[str, Any] = {
        "execution_id": "exec_test0000000000000000",
        "trace_id": "a" * 32,
        "actor_id": "agt_abc123",
        "actor_type": "agent",
    }

    def _request(metadata: dict[str, Any]) -> UpstreamExecRequest:
        return UpstreamExecRequest(
            method="GET",
            url="https://api.example.com/v1/things",
            headers={},
            body=None,
            timeout_s=30.0,
            metadata=metadata,
        )

    attributed = _ctx_from_metadata(
        _request({**base_metadata, "credential_id": "cred_abc", "credential_name": "stripe-live"})
    )
    assert attributed.credential_id == "cred_abc"
    assert attributed.credential_name == "stripe-live"

    bare = _ctx_from_metadata(_request(base_metadata))
    assert bare.credential_id is None
    assert bare.credential_name is None


def test_ctx_from_metadata_mints_a_valid_trace_id_for_garbage() -> None:
    """Metadata without a usable trace_id yields a minted 32-hex id — never the
    literal "unknown", which would be persisted on the execution record and
    crash any emit site that trusts the context (#903)."""
    for metadata in (
        {"actor_id": "agt_abc123", "actor_type": "agent"},
        {"actor_id": "agt_abc123", "actor_type": "agent", "trace_id": "unknown"},
    ):
        ctx_req = _ctx_from_metadata(
            UpstreamExecRequest(
                method="GET",
                url="https://api.example.com/v1/things",
                headers={},
                body=None,
                timeout_s=30.0,
                metadata=metadata,
            )
        )
        assert re.fullmatch(r"[0-9a-f]{32}", ctx_req.trace_id)

    passthrough = _ctx_from_metadata(
        UpstreamExecRequest(
            method="GET",
            url="https://api.example.com/v1/things",
            headers={},
            body=None,
            timeout_s=30.0,
            metadata={"actor_id": "agt_abc123", "actor_type": "agent", "trace_id": "a" * 32},
        )
    )
    assert passthrough.trace_id == "a" * 32


def test_ctx_from_metadata_rebuilds_operation() -> None:
    """The metadata's ``operation`` dict rebuilds a full OperationInfo, and no
    ``operation`` dict leaves the context operation-less."""

    def _ctx(metadata: dict[str, Any]) -> ExecuteRequestContext:
        return _ctx_from_metadata(
            UpstreamExecRequest(
                method="GET",
                url="https://api.example.com/v1/things",
                headers={},
                body=None,
                timeout_s=30.0,
                metadata={"actor_id": "agt_abc123", "actor_type": "agent", **metadata},
            )
        )

    full = _ctx({"operation": {"id": "op_x", "path": "/v1/things/{id}", "method": "GET"}})
    assert full.operation == OperationInfo(id="op_x", path="/v1/things/{id}", method="GET")

    assert _ctx({}).operation is None


class _StatusBroker(_SpyBroker):
    def __init__(self, status_code: int) -> None:
        super().__init__()
        self._status_code = status_code

    async def execute(self, request: RunnerRequest, context: ExecutionContext) -> ExecutionOutcome:
        self.calls.append((request, context))
        return ExecutionOutcome(
            result=SharedRunnerResult(
                status_code=self._status_code,
                body=b"",
                headers={},
                content_type="text/plain",
                duration_ms=2,
            ),
            context=context,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("broker", "api_vendor", "expected_type", "expected_tags", "expected_service_types"),
    [
        pytest.param(
            _SpyBroker(),
            "example",
            EventType.EXECUTION_COMPLETED,
            {Origin.MCP},
            [],
            id="completed",
        ),
        pytest.param(
            _StatusBroker(401),
            "example",
            EventType.EXECUTION_FAILED,
            {ErrorSource.AUTH_THIRDPARTY_UNAUTHORIZED, Origin.MCP},
            [],
            id="upstream-401",
        ),
        pytest.param(
            _StatusBroker(403),
            "example",
            EventType.EXECUTION_FAILED,
            {ErrorSource.AUTH_THIRDPARTY_FORBIDDEN, Origin.MCP},
            [],
            id="upstream-403",
        ),
        pytest.param(
            _StatusBroker(401),
            None,
            EventType.EXECUTION_FAILED,
            {Origin.MCP},
            [],
            id="upstream-401-vendorless",
        ),
        pytest.param(
            _RaisingBroker(),
            "example",
            EventType.EXECUTION_FAILED,
            {Origin.MCP},
            [EventType.UPSTREAM_CIRCUIT_OPEN],
            id="broker-error",
        ),
    ],
)
async def test_async_execution_emits_lifecycle_event_once(
    broker: _SpyBroker,
    api_vendor: str | None,
    expected_type: str,
    expected_tags: set[Any],
    expected_service_types: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins #1788: an async execution through ``ExecutionHandler`` and
    ``PipelineExecutor`` emits exactly one ``execution.completed`` /
    ``execution.failed`` event (carrying ``job_id``), never a second one from
    ``run_execution``."""
    broker.calls.clear()
    _circuit_event_last_emitted.clear()
    registry = RunnerRegistry()
    registry.register(["http", "https"], _NoopRunner(), required=True)
    handler = ExecutionHandler(
        executor=PipelineExecutor(registry, broker_factory=lambda _runner: broker)
    )
    session = AsyncMock()
    session.add = MagicMock()
    db = SessionScope(session)

    service_emit = AsyncMock()
    handler_emit = AsyncMock()
    monkeypatch.setattr(
        "jentic_one.broker.services.execution.service.record_execution", AsyncMock()
    )
    monkeypatch.setattr("jentic_one.broker.services.execution.service.emit_event", service_emit)
    monkeypatch.setattr("jentic_one.shared.jobs.execution_handler.emit_event", handler_emit)
    monkeypatch.setattr(
        "jentic_one.shared.jobs.execution_handler.maybe_emit_repeated_failure", AsyncMock()
    )

    try:
        await handler.execute(
            "job_once",
            db,
            payload={
                "execution_id": "exec_once0000000000000000",
                "upstream_url": "https://api.example.com/v1/things",
                "method": "GET",
                "trace_id": "d" * 32,
                "api_vendor": api_vendor,
                "api_name": "api",
                "api_version": "1.0.0",
                "origin": "mcp",
            },
            created_by="agt_abc123",
            actor_type="agent",
        )
    finally:
        _circuit_event_last_emitted.clear()

    assert [c.kwargs.get("type") for c in service_emit.call_args_list] == expected_service_types
    handler_emit.assert_awaited_once()
    kwargs = handler_emit.call_args.kwargs
    assert kwargs["type"] == expected_type
    assert kwargs["job_id"] == "job_once"
    assert kwargs["execution_id"] == "exec_once0000000000000000"
    assert kwargs["tags"] == expected_tags
