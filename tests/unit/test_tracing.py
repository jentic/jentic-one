"""Unit tests for shared tracing configuration + the §04 outbound-tracing slice."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
import structlog
import yaml
from fastapi import FastAPI
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
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
    EXCEPTION_MESSAGE_PLACEHOLDER,
    JENTIC_TRACESTATE_KEY,
    ScrubbingSpanExporter,
    configure_tracing,
    current_trace_id,
    instrument_inbound_app,
    instrument_outbound_client,
    jentic_tracestate,
    pack_jentic_tracestate,
    reset_tracing,
    scrub_span,
    scrubbing_exporter,
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
    # example == "jentic=exec_xyz789:_:stripe:payments:2023-10-16" — the second
    # (toolkit) segment is a legacy slot, always `_` since theme-5 Phase 6b.
    key, _, member = example.partition("=")
    assert key == JENTIC_TRACESTATE_KEY

    packed = pack_jentic_tracestate(
        execution_id="exec_xyz789",
        toolkit_id=None,
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
async def test_outbound_span_url_masks_query_param_api_key():
    """A ``location=query`` API key rides the upstream query string; the span's
    recorded URL keeps host and path but never the value."""
    exporter = InMemorySpanExporter()
    provider = configure_tracing("test-service", _NONE_CONFIG)
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(200)))
    instrument_outbound_client(client)
    try:
        await client.get("https://upstream.example/v1/search?api_key=qk_live_secret&q=term")
    finally:
        await client.aclose()

    spans = exporter.get_finished_spans()
    assert spans, "expected at least one outbound span"
    attrs = {k: v for span in spans for k, v in (span.attributes or {}).items()}
    blob = "\n".join(f"{k}={v}" for k, v in attrs.items())
    assert "qk_live_secret" not in blob
    assert "term" not in blob
    url_values = [str(attrs[k]) for k in ("http.url", "url.full") if k in attrs]
    assert url_values, "the instrumentor records the request URL"
    for value in url_values:
        assert value.startswith("https://upstream.example/v1/search?api_key=")


def test_sync_outbound_client_span_url_masks_query_param_api_key():
    """A sync ``httpx.Client`` gets the plain (non-coroutine) hooks — the
    instrumentor drops coroutine hooks there, which would record the raw URL."""
    exporter = InMemorySpanExporter()
    provider = configure_tracing("test-service", _NONE_CONFIG)
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    client = httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200)))
    instrument_outbound_client(client)
    try:
        client.get("https://upstream.example/v1/search?api_key=qk_live_secret")
    finally:
        client.close()

    spans = exporter.get_finished_spans()
    assert spans, "expected at least one outbound span"
    attrs = {k: v for span in spans for k, v in (span.attributes or {}).items()}
    assert "qk_live_secret" not in "\n".join(f"{k}={v}" for k, v in attrs.items())
    url_values = [str(attrs[k]) for k in ("http.url", "url.full") if k in attrs]
    assert url_values, "the instrumentor records the request URL"


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


# --------------------------------------------------------------------------- #
# Export-time scrubbing of failed outbound requests
# --------------------------------------------------------------------------- #

_HEADER_SECRET = "hv_live_7Qx9secret"
_QUERY_SECRET = "qv_live_4Kp2secret"
_SECRET_URL = f"https://upstream.example/v1/charges?api_key={_QUERY_SECRET}&q=term"
_SECRETS = (_HEADER_SECRET, _QUERY_SECRET)


def _exporting_provider() -> tuple[TracerProvider, InMemorySpanExporter]:
    """Provider built by the production ``configure_tracing`` chain
    (``BatchSpanProcessor`` → ``ScrubbingSpanExporter``) over an in-memory sink."""
    exporter = InMemorySpanExporter()
    provider = configure_tracing("test-service", exporter=exporter)
    return provider, exporter


def _exported(provider: TracerProvider, exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    assert provider.force_flush()
    spans = list(exporter.get_finished_spans())
    assert spans, "expected exported spans"
    return spans


def _span_text(span: ReadableSpan) -> str:
    """Every string an exporter would ship for ``span``, flattened."""
    parts = [span.name, str(span.status.description or "")]
    parts.extend(f"{k}={v}" for k, v in (span.attributes or {}).items())
    for event in span.events:
        parts.append(event.name)
        parts.extend(f"{k}={v}" for k, v in (event.attributes or {}).items())
    for link in span.links:
        parts.extend(f"{k}={v}" for k, v in (link.attributes or {}).items())
    return "\n".join(parts)


def _assert_no_request_data(spans: list[ReadableSpan]) -> None:
    for span in spans:
        text = _span_text(span)
        for secret in _SECRETS:
            assert secret not in text, f"{secret!r} exported on span {span.name!r}:\n{text}"


def _exception_events(span: ReadableSpan) -> list[dict[str, object]]:
    return [dict(e.attributes or {}) for e in span.events if e.name == "exception"]


def _failing_handler(
    exc_factory: Callable[[httpx.Request], Exception],
) -> Callable[[httpx.Request], httpx.Response]:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise exc_factory(request)

    return _handler


class _UpstreamAuthFlowError(Exception):
    """Stand-in for a custom transport/auth-flow error that chains request material."""


def _chained_error(request: httpx.Request) -> Exception:
    try:
        raise httpx.ReadError(f"read failed for {request.url} ({request.headers['x-api-key']})")
    except httpx.ReadError as inner:
        outer = _UpstreamAuthFlowError(f"auth flow aborted: {inner}")
        outer.__cause__ = inner
        return outer


_TRANSPORT_FAILURES = [
    pytest.param(
        lambda r: httpx.LocalProtocolError(f"Illegal header value b'{r.headers['x-api-key']}'"),
        "httpx.LocalProtocolError",
        id="illegal-header-value",
    ),
    pytest.param(
        lambda r: httpx.ProxyError(f"proxy refused {r.url} with {r.headers['x-api-key']}"),
        "httpx.ProxyError",
        id="proxy-error",
    ),
    pytest.param(_chained_error, f"{__name__}._UpstreamAuthFlowError", id="chained-cause"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("exc_factory", "exc_type"), _TRANSPORT_FAILURES)
async def test_failed_async_outbound_request_exports_no_request_data(exc_factory, exc_type):
    """Exception text quoting a header value / query string never reaches an
    exported span — neither the client span nor the parent it propagates to —
    while the exception type and frame locations survive."""
    provider, exporter = _exporting_provider()
    client = httpx.AsyncClient(transport=httpx.MockTransport(_failing_handler(exc_factory)))
    instrument_outbound_client(client)
    tracer = provider.get_tracer("test")
    try:
        with (
            pytest.raises((httpx.HTTPError, _UpstreamAuthFlowError)),
            tracer.start_as_current_span("broker.upstream_request"),
        ):
            await client.get(_SECRET_URL, headers={"x-api-key": _HEADER_SECRET})
    finally:
        await client.aclose()

    spans = _exported(provider, exporter)
    _assert_no_request_data(spans)
    assert {s.kind for s in spans} >= {trace.SpanKind.CLIENT, trace.SpanKind.INTERNAL}
    for span in spans:
        events = _exception_events(span)
        assert events, f"span {span.name!r} lost its exception event"
        assert events[-1]["exception.type"] == exc_type
        assert events[-1]["exception.message"] == EXCEPTION_MESSAGE_PLACEHOLDER
        assert 'File "' in str(events[-1]["exception.stacktrace"])
        assert span.status.status_code is trace.StatusCode.ERROR
        assert span.status.description == exc_type


@pytest.mark.parametrize(("exc_factory", "exc_type"), _TRANSPORT_FAILURES)
def test_failed_sync_outbound_request_exports_no_request_data(exc_factory, exc_type):
    """Same guarantee for a sync ``httpx.Client`` (plain hooks, sync transport)."""
    provider, exporter = _exporting_provider()
    client = httpx.Client(transport=httpx.MockTransport(_failing_handler(exc_factory)))
    instrument_outbound_client(client)
    try:
        with pytest.raises((httpx.HTTPError, _UpstreamAuthFlowError)):
            client.get(_SECRET_URL, headers={"x-api-key": _HEADER_SECRET})
    finally:
        client.close()

    spans = _exported(provider, exporter)
    _assert_no_request_data(spans)
    (client_span,) = [s for s in spans if s.kind is trace.SpanKind.CLIENT]
    assert _exception_events(client_span)[-1]["exception.type"] == exc_type


@pytest.mark.asyncio
async def test_real_transport_header_value_error_exports_no_header_value():
    """The real h11 transport rejects an illegal header value with a message that
    quotes it (``Illegal header value b'…'``); the exported spans must not."""
    provider, exporter = _exporting_provider()

    async def _accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    server = await asyncio.start_server(_accept, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = httpx.AsyncClient()
    instrument_outbound_client(client)
    tracer = provider.get_tracer("test")
    try:
        with (
            pytest.raises(httpx.LocalProtocolError, match=_HEADER_SECRET),
            tracer.start_as_current_span("broker.upstream_request"),
        ):
            await client.get(
                f"http://127.0.0.1:{port}/v1?api_key={_QUERY_SECRET}",
                headers={"x-api-key": f"{_HEADER_SECRET}\x00"},
            )
    finally:
        await client.aclose()
        server.close()
        await server.wait_closed()

    spans = _exported(provider, exporter)
    _assert_no_request_data(spans)
    client_span = next(s for s in spans if s.kind is trace.SpanKind.CLIENT)
    assert _exception_events(client_span)[-1]["exception.type"] == "httpx.LocalProtocolError"


@pytest.mark.asyncio
async def test_http_status_error_on_parent_span_exports_no_url():
    """``raise_for_status`` quotes the full URL (query included); recorded on
    the enclosing span it must not carry the query value."""
    provider, exporter = _exporting_provider()
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(401)))
    instrument_outbound_client(client)
    tracer = provider.get_tracer("test")
    try:
        with (
            pytest.raises(httpx.HTTPStatusError, match=_QUERY_SECRET),
            tracer.start_as_current_span("oauth.token_request"),
        ):
            response = await client.get(_SECRET_URL, headers={"x-api-key": _HEADER_SECRET})
            response.raise_for_status()
    finally:
        await client.aclose()

    spans = _exported(provider, exporter)
    _assert_no_request_data(spans)
    parent = next(s for s in spans if s.name == "oauth.token_request")
    assert _exception_events(parent)[-1]["exception.type"] == "httpx.HTTPStatusError"
    assert parent.status.description == "httpx.HTTPStatusError"


@pytest.mark.asyncio
async def test_captured_client_headers_are_limited_to_safe_list(monkeypatch):
    """Header capture switched on via the instrumentor's env var never exports a
    non-safe-listed header value on an outbound span."""
    monkeypatch.setenv("OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_CLIENT_REQUEST", ".*")
    monkeypatch.setenv("OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_CLIENT_RESPONSE", ".*")
    provider, exporter = _exporting_provider()

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"x-echo-key": _HEADER_SECRET})

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    instrument_outbound_client(client)
    try:
        await client.get(_SECRET_URL, headers={"x-api-key": _HEADER_SECRET})
    finally:
        await client.aclose()

    spans = _exported(provider, exporter)
    _assert_no_request_data(spans)
    attrs = next(s for s in spans if s.kind is trace.SpanKind.CLIENT).attributes or {}
    assert "http.request.header.user-agent" in attrs


def test_scrubbing_exporter_wrap_is_idempotent():
    inner = InMemorySpanExporter()
    wrapped = scrubbing_exporter(inner)
    assert isinstance(wrapped, ScrubbingSpanExporter)
    assert scrubbing_exporter(wrapped) is wrapped
    assert wrapped.delegate is inner


# --------------------------------------------------------------------------- #
# Traceback frames, OTLP encoding, inbound URLs, exporter contract
# --------------------------------------------------------------------------- #


def _stacktrace_of(provider: TracerProvider, exporter: InMemorySpanExporter) -> str:
    (span,) = _exported(provider, exporter)
    return str(_exception_events(span)[-1]["exception.stacktrace"])


def _real_frame_file() -> str:
    return str(Path(asyncio.__file__).with_name("base_events.py"))


@pytest.mark.parametrize(
    "forged_frame",
    [
        pytest.param(f'  File "/srv/{_HEADER_SECRET}.py", line 1, in handler', id="unknown-path"),
        pytest.param(
            f'  File "{_real_frame_file()}", line 1, in {_HEADER_SECRET}', id="unknown-func"
        ),
        pytest.param(f'  File "{_real_frame_file()}", line 9999999, in run_forever', id="bad-line"),
    ],
)
def test_frame_shaped_message_lines_are_not_exported(forged_frame):
    """An exception message whose lines merely look like traceback frames is
    dropped with the rest of the message; the real frames survive."""
    provider, exporter = _exporting_provider()
    tracer = provider.get_tracer("test")
    with (
        pytest.raises(ValueError, match="rejected"),
        tracer.start_as_current_span("ingest.stage parse"),
    ):
        raise ValueError(f"rejected\n{forged_frame}")

    stacktrace = _stacktrace_of(provider, exporter)
    assert forged_frame not in stacktrace
    assert _HEADER_SECRET not in stacktrace
    assert "9999999" not in stacktrace
    assert f"in {test_frame_shaped_message_lines_are_not_exported.__name__}" in stacktrace


def test_scrubbed_span_encodes_with_otlp_keeping_structure(monkeypatch):
    """The rebuilt span serialises through the real OTLP protobuf encoder with
    context, parent, kind, resource, scope, links, times and dropped counts
    intact — and no exception text in the encoded bytes."""
    monkeypatch.setenv("OTEL_SPAN_ATTRIBUTE_COUNT_LIMIT", "2")
    monkeypatch.setenv("OTEL_SPAN_EVENT_COUNT_LIMIT", "2")
    monkeypatch.setenv("OTEL_SPAN_LINK_COUNT_LIMIT", "1")
    provider, exporter = _exporting_provider()
    tracer = provider.get_tracer("test.scope", "1.2.3")
    link_ctx = SpanContext(
        trace_id=0x1234, span_id=0x5678, is_remote=True, trace_flags=TraceFlags(1)
    )
    with tracer.start_as_current_span("broker.execute") as parent:
        parent_ctx = parent.get_span_context()
        with (
            pytest.raises(RuntimeError),
            tracer.start_as_current_span(
                "broker.upstream_request",
                kind=trace.SpanKind.CLIENT,
                attributes={"a": 1, "b": 2, "url.full": _SECRET_URL},
                links=[
                    trace.Link(link_ctx, {"link.kind": "dropped"}),
                    trace.Link(link_ctx, {"link.kind": "kept"}),
                ],
            ) as span,
        ):
            span.add_event("first")
            span.add_event("second")
            raise RuntimeError(f"upstream said {_HEADER_SECRET}")

    child = next(s for s in _exported(provider, exporter) if s.name == "broker.upstream_request")
    request = encode_spans([child])
    assert _HEADER_SECRET.encode() not in request.SerializeToString()
    (resource_spans,) = request.resource_spans
    assert {kv.key: kv.value.string_value for kv in resource_spans.resource.attributes}[
        "service.name"
    ] == "test-service"
    (scope_spans,) = resource_spans.scope_spans
    assert (scope_spans.scope.name, scope_spans.scope.version) == ("test.scope", "1.2.3")
    (pb,) = scope_spans.spans
    assert pb.trace_id == parent_ctx.trace_id.to_bytes(16, "big")
    assert pb.span_id == child.context.span_id.to_bytes(8, "big")
    assert pb.parent_span_id == parent_ctx.span_id.to_bytes(8, "big")
    assert pb.kind == pb.SpanKind.SPAN_KIND_CLIENT
    assert 0 < pb.start_time_unix_nano <= pb.end_time_unix_nano
    assert (pb.dropped_attributes_count, pb.dropped_events_count, pb.dropped_links_count) == (
        1,
        1,
        1,
    )
    assert [e.name for e in pb.events] == ["second", "exception"]
    (link,) = pb.links
    assert link.span_id == (0x5678).to_bytes(8, "big")
    assert [(kv.key, kv.value.string_value) for kv in link.attributes] == [("link.kind", "kept")]
    assert pb.status.code == pb.status.STATUS_CODE_ERROR
    assert pb.status.message == "RuntimeError"


@pytest.mark.asyncio
async def test_inbound_server_span_masks_query_values():
    """An inbound callback's query values (OAuth ``code``/``state``) are masked
    on the exported server span; parameter names, path and route survive."""
    provider, exporter = _exporting_provider()
    app = FastAPI()

    @app.get("/oauth/callback")
    async def _callback() -> dict[str, str]:
        return {}

    instrument_inbound_app(app)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://svc"
        ) as client:
            response = await client.get(
                f"/oauth/callback?code={_QUERY_SECRET}&state={_HEADER_SECRET}"
            )
        assert response.status_code == 200
    finally:
        FastAPIInstrumentor.uninstrument_app(app)

    spans = _exported(provider, exporter)
    _assert_no_request_data(spans)
    server = next(s for s in spans if s.kind is trace.SpanKind.SERVER)
    attrs = server.attributes or {}
    assert attrs["http.url"] == "http://svc/oauth/callback?code=***REDACTED***&state=***REDACTED***"
    assert attrs["http.target"] == "/oauth/callback"
    assert attrs["http.route"] == "/oauth/callback"


def _finished_span(attributes: dict[str, str]) -> ReadableSpan:
    return ReadableSpan(
        name="GET /device",
        context=SpanContext(trace_id=1, span_id=2, is_remote=False),
        resource=Resource.create({}),
        attributes=attributes,
        kind=trace.SpanKind.SERVER,
        start_time=1,
        end_time=2,
    )


def test_scrub_masks_stable_semconv_url_and_query_attributes():
    """The stable-semconv inbound attributes (``url.query``/``url.full``) and a
    target carrying a query are masked too; unrelated attributes are untouched."""
    span = scrub_span(
        _finished_span(
            {
                "url.query": f"user_code={_QUERY_SECRET}&{_HEADER_SECRET}",
                "url.full": f"https://svc/device?user_code={_QUERY_SECRET}",
                "http.target": f"/device?user_code={_QUERY_SECRET}",
                "url.path": "/device",
                "http.request.header.x_jentic_api_key": _HEADER_SECRET,
                "http.request.header.content_type": "application/json",
            }
        )
    )
    assert dict(span.attributes or {}) == {
        "url.query": "user_code=***REDACTED***&***REDACTED***",
        "url.full": "https://svc/device?user_code=***REDACTED***",
        "http.target": "/device?user_code=***REDACTED***",
        "url.path": "/device",
        "http.request.header.content_type": "application/json",
    }


def test_scrub_failure_drops_only_that_span_and_logs(monkeypatch):
    """A span the scrub cannot process is never exported unscrubbed; the rest
    of the batch still goes out and the drop is logged."""
    provider, exporter = _exporting_provider()
    real_scrub = scrub_span

    def _flaky_scrub(span: ReadableSpan) -> ReadableSpan:
        if span.name == "bad":
            raise TypeError("unexpected attribute shape")
        return real_scrub(span)

    monkeypatch.setattr("jentic_one.shared.tracing.scrub_span", _flaky_scrub)
    tracer = provider.get_tracer("test")
    for name in ("good", "bad", "also-good"):
        with tracer.start_as_current_span(name):
            pass

    with structlog.testing.capture_logs() as logs:
        spans = _exported(provider, exporter)
    assert [s.name for s in spans] == ["good", "also-good"]
    assert {
        "event": "span_scrub_failed_span_dropped",
        "span_name": "bad",
        "error_type": "TypeError",
        "log_level": "warning",
    } in logs


def test_scrubbing_exporter_forwards_result_flush_and_shutdown():
    calls: list[str] = []

    class _Recorder(InMemorySpanExporter):
        def export(self, spans):
            calls.append(f"export:{len(spans)}")
            return SpanExportResult.FAILURE

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            calls.append(f"flush:{timeout_millis}")
            return False

        def shutdown(self) -> None:
            calls.append("shutdown")

    wrapped = scrubbing_exporter(_Recorder())
    assert wrapped.export([_finished_span({})]) is SpanExportResult.FAILURE
    assert wrapped.force_flush(123) is False
    wrapped.shutdown()
    assert calls == ["export:1", "flush:123", "shutdown"]
