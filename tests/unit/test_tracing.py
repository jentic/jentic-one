"""Unit tests for shared tracing configuration + the §04 outbound-tracing slice."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import yaml
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    TraceFlags,
    set_span_in_context,
)
from opentelemetry.trace.span import TraceState

from jentic_one.shared.config import TracingConfig
from jentic_one.shared.tracing import (
    JENTIC_TRACESTATE_KEY,
    configure_tracing,
    current_trace_id,
    instrument_outbound_client,
    jentic_tracestate,
    pack_jentic_tracestate,
    reset_tracing,
)

_NONE_CONFIG = TracingConfig(exporter="none")
_OPENAPI_SPEC = Path(__file__).resolve().parents[2] / "openapi" / "broker" / "broker.openapi.yaml"


@pytest.fixture(autouse=True)
def _reset_tracer_provider():
    """Reset OTel global tracer provider between tests."""
    reset_tracing()
    yield
    reset_tracing()


def test_configure_tracing_sets_tracer_provider():
    provider = configure_tracing("test-service")
    assert isinstance(provider, TracerProvider)
    assert trace.get_tracer_provider() is provider


def test_configure_tracing_idempotent():
    first_provider = configure_tracing("test-service")
    second_provider = configure_tracing("test-service")
    assert first_provider is second_provider


# --------------------------------------------------------------------------- #
# current_trace_id
# --------------------------------------------------------------------------- #


def test_current_trace_id_formats_the_active_span_id_as_32_hex():
    span_context = SpanContext(
        trace_id=0x801384A0E0EB70D0C180CD38BCBA4D38,
        span_id=0x9BAAEE8F1FB43393,
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    token = otel_context.attach(set_span_in_context(NonRecordingSpan(span_context)))
    try:
        assert current_trace_id() == "801384a0e0eb70d0c180cd38bcba4d38"
    finally:
        otel_context.detach(token)


def test_current_trace_id_is_none_without_a_valid_span():
    """Outside a span (or with tracing unconfigured) callers get None, not garbage."""
    assert current_trace_id() is None


# --------------------------------------------------------------------------- #
# jentic= tracestate packing
# --------------------------------------------------------------------------- #


def test_pack_jentic_tracestate_matches_openapi_example():
    """The packed value must match the documented OpenAPI Tracestate example.

    Contract test: the format lives in one place and is guarded against drift
    from broker.openapi.yaml (the spec the agent caller reads).
    """
    spec = yaml.safe_load(_OPENAPI_SPEC.read_text())
    example = spec["components"]["headers"]["Tracestate"]["schema"]["examples"][0]
    # example == "jentic=exec_xyz789:tk_abc123:stripe:payments:2023-10-16"
    key, _, member = example.partition("=")
    assert key == JENTIC_TRACESTATE_KEY

    packed = pack_jentic_tracestate(
        execution_id="exec_xyz789",
        toolkit_id="tk_abc123",
        vendor="stripe",
        name="payments",
        version="2023-10-16",
    )
    assert packed == member


def test_pack_jentic_tracestate_fills_missing_segments_with_placeholder():
    """A fixed five-field shape is preserved when segments are absent."""
    packed = pack_jentic_tracestate(
        execution_id="exec_1",
        toolkit_id=None,
        vendor=None,
        name=None,
        version=None,
    )
    assert packed == "exec_1:_:_:_:_"
    assert len(packed.split(":")) == 5


# --------------------------------------------------------------------------- #
# jentic_tracestate context activation
# --------------------------------------------------------------------------- #


def test_jentic_tracestate_activates_member_inside_a_span():
    """Inside a recording span the jentic member is on the current tracestate."""
    provider = configure_tracing("test-service", _NONE_CONFIG)
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("call"):
        with jentic_tracestate("exec_1:tk_1:stripe:payments:v1"):
            current = trace.get_current_span().get_span_context()
            assert current.trace_state.get(JENTIC_TRACESTATE_KEY) == (
                "exec_1:tk_1:stripe:payments:v1"
            )
        # restored after the block
        after = trace.get_current_span().get_span_context()
        assert after.trace_state.get(JENTIC_TRACESTATE_KEY) is None


def test_jentic_tracestate_is_a_noop_without_a_span():
    """Outside any span (no valid context) the helper yields without raising."""
    with jentic_tracestate("exec_1:_:_:_:_"):
        assert not trace.get_current_span().get_span_context().is_valid


def test_jentic_tracestate_composes_with_existing_vendor_entries():
    """A pre-existing *other* vendor member is preserved alongside jentic."""
    configure_tracing("test-service", _NONE_CONFIG)
    seeded = SpanContext(
        trace_id=0x1,
        span_id=0x2,
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
        trace_state=TraceState([("datadog", "s:1")]),
    )
    ctx = set_span_in_context(NonRecordingSpan(seeded))
    token = otel_context.attach(ctx)
    try:
        with jentic_tracestate("exec_1:tk_1:stripe:payments:v1"):
            state = trace.get_current_span().get_span_context().trace_state
            assert state.get(JENTIC_TRACESTATE_KEY) == "exec_1:tk_1:stripe:payments:v1"
            assert state.get("datadog") == "s:1"
    finally:
        otel_context.detach(token)


# --------------------------------------------------------------------------- #
# Span-attribute redaction on the instrumented outbound client
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_outbound_spans_redact_secrets_and_bodies():
    """A request bearing Authorization + a body must leak neither onto a span."""
    exporter = InMemorySpanExporter()
    provider = configure_tracing("test-service", _NONE_CONFIG)
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json", "set-cookie": "session=secret"},
            json={"ok": True},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    instrument_outbound_client(client)
    try:
        await client.post(
            "https://upstream.example/v1/charges",
            headers={"authorization": "Bearer sk_live_secret", "x-api-key": "key_secret"},
            content=b'{"amount": 100}',
        )
    finally:
        await client.aclose()

    spans = exporter.get_finished_spans()
    assert spans, "expected at least one outbound span"
    blob = "\n".join(
        f"{k}={v}" for span in spans for k, v in (span.attributes or {}).items()
    ).lower()
    # No secret-bearing header or body content on any span attribute.
    assert "sk_live_secret" not in blob
    assert "key_secret" not in blob
    assert "session=secret" not in blob
    assert "amount" not in blob
    assert "authorization" not in blob
    assert "set-cookie" not in blob


@pytest.mark.asyncio
async def test_outbound_request_carries_w3c_and_jentic_tracestate():
    """Propagation: the outbound request gets traceparent + the jentic member."""
    provider = configure_tracing("test-service", _NONE_CONFIG)
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    instrument_outbound_client(client)
    tracer = provider.get_tracer("test")
    try:
        with (
            tracer.start_as_current_span("call"),
            jentic_tracestate("exec_9:tk_9:stripe:payments:v3"),
        ):
            await client.get("https://upstream.example/v1/ping")
    finally:
        await client.aclose()

    assert "traceparent" in captured
    assert JENTIC_TRACESTATE_KEY in captured.get("tracestate", "")
    assert "exec_9:tk_9:stripe:payments:v3" in captured["tracestate"]
