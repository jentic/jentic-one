"""Unit tests asserting OTel spans and structlog events for broker execution hot-path."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest
import structlog
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from jentic_one.broker.adapters.runners.base import RunnerRequest, RunnerResult, UpstreamRunner
from jentic_one.broker.core.schemas import ExecuteRequestContext
from jentic_one.broker.services.execution.service import default_broker, run_execution


class _StubRunner(UpstreamRunner):
    async def run(self, request: RunnerRequest) -> RunnerResult:
        return RunnerResult(
            status_code=200,
            headers={},
            body=b"ok",
            content_type="text/plain",
            duration_ms=5,
        )


def _ctx_req() -> ExecuteRequestContext:
    return ExecuteRequestContext(
        upstream_url="https://api.example.com/v1/things",
        method="GET",
        trace_id="a" * 32,
        toolkit_id="tk_test000000000000000000",
        operation_id="getThing",
        api_vendor="example",
        api_name="api",
        api_version="1.0.0",
        prefer=None,
        pinned_revisions=None,
    )


@pytest.fixture
def span_exporter() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with patch("jentic_one.broker.services.execution.service._tracer", provider.get_tracer("test")):
        yield exporter
    provider.shutdown()


@pytest.mark.asyncio
async def test_broker_execute_span_created(span_exporter: InMemorySpanExporter) -> None:
    """The broker.execute span is created with expected attributes."""
    session = AsyncMock()
    broker = default_broker(_StubRunner())

    with patch(
        "jentic_one.broker.services.execution.service.record_execution", new_callable=AsyncMock
    ):
        await run_execution(
            _ctx_req(),
            body=None,
            headers=None,
            session=session,
            broker=broker,
            actor_id="agt_abc123",
            actor_type="agent",
        )

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "broker.execute"
    attrs = dict(span.attributes or {})
    assert attrs["execution_id"]
    assert attrs["operation_id"] == "getThing"
    assert attrs["toolkit_id"] == "tk_test000000000000000000"
    assert attrs["api_vendor"] == "example"


@pytest.mark.asyncio
async def test_execution_started_and_finished_log_events() -> None:
    """Structured log events execution_started and execution_finished fire."""
    session = AsyncMock()
    broker = default_broker(_StubRunner())

    factory = structlog.testing.CapturingLoggerFactory()

    with (
        patch(
            "jentic_one.broker.services.execution.service.record_execution",
            new_callable=AsyncMock,
        ),
        patch(
            "jentic_one.broker.services.execution.service.logger",
            structlog.wrap_logger(factory.logger, processors=[]),
        ),
    ):
        await run_execution(
            _ctx_req(),
            body=None,
            headers=None,
            session=session,
            broker=broker,
            actor_id="agt_abc123",
            actor_type="agent",
        )

    events = [entry.method_name for entry in factory.logger.calls]
    assert "info" in events
    event_names = [entry.kwargs.get("event") or entry.args[0] for entry in factory.logger.calls]
    assert "execution_started" in event_names
    assert "execution_finished" in event_names
