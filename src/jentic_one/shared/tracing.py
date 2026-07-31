"""OpenTelemetry tracing — the single OTel *tracing* home (facade).

Like ``shared/metrics.py`` for metrics, this module is the **only** sanctioned
place to import the OTel tracing SDK + instrumentation. No source module outside
this file may import ``opentelemetry.instrumentation.*`` or the OTel propagator
APIs directly — enforced by ``tests/arch/test_tracing_facade.py``. Centralising
the instrumentation here means the **redaction hooks can't be forgotten** by a
new call site (a compliance requirement — the broker proxies ``Authorization``,
injected API keys, cookies, and arbitrary tenant bodies that may carry PII).

It owns three concerns (§04 PR-B tracing slice):

1. ``configure_tracing`` — the global ``TracerProvider`` (OTLP gRPC or no-op).
2. ``instrument_outbound_client`` — W3C ``traceparent``/``tracestate`` propagation
   *into* the upstream over the shared ``httpx`` client, with span-attribute
   redaction (no bodies; headers via a safe-list only).
3. ``pack_jentic_tracestate`` / ``jentic_tracestate`` — the ``jentic=``
   vendor ``tracestate`` member (``exec:tk:vendor:name:version``) the broker must
   pack itself (the instrumentor only propagates standard W3C keys).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from jentic_one.shared.config import TracingConfig

if TYPE_CHECKING:
    import httpx
    from opentelemetry.instrumentation.httpx import RequestInfo, ResponseInfo
    from opentelemetry.trace import Span

# ---------------------------------------------------------------------------
# Span-attribute redaction
# ---------------------------------------------------------------------------

# Request/response headers that are safe to record as span attributes — only
# structural metadata, never anything bearing a secret or PII. Everything not on
# this list (``authorization``, ``cookie``, ``set-cookie``, ``x-api-key``,
# injected-credential names, …) is dropped. Lower-cased for case-insensitive
# matching against the wire header name.
_SAFE_SPAN_HEADERS: frozenset[str] = frozenset(
    {
        "content-type",
        "content-length",
        "content-encoding",
        "traceparent",
        "tracestate",
        "user-agent",
    }
)


def _safe_header_attributes(headers: httpx.Headers, *, prefix: str) -> dict[str, str]:
    """Project an httpx header mapping down to the safe-listed structural subset.

    Used by the outbound (httpx) request/response hooks so one safe-list governs
    what reaches a span. Bodies are never read.
    """
    out: dict[str, str] = {}
    for name, value in headers.items():
        if name.lower() in _SAFE_SPAN_HEADERS:
            out[f"{prefix}.{name.lower()}"] = value
    return out


def _redact_request_span(span: Span, info: RequestInfo) -> None:
    """Outbound request hook — record only safe-listed structural attributes.

    The instrumentor records method/url/host structurally already; this hook
    never adds the request body and only mirrors safe-listed headers, so a
    proxied ``Authorization`` / injected API key / cookie never lands on a span.
    """
    if not span.is_recording() or info.headers is None:
        return
    for key, value in _safe_header_attributes(info.headers, prefix="http.request.header").items():
        span.set_attribute(key, value)


def _redact_response_span(span: Span, _request: RequestInfo, info: ResponseInfo) -> None:
    """Outbound response hook — safe-listed response headers only, never a body.

    Drops ``set-cookie`` and any other secret-bearing response header; the body
    stream is never consumed (consuming it here would also break the
    passthrough).
    """
    if not span.is_recording() or info.headers is None:
        return
    for key, value in _safe_header_attributes(info.headers, prefix="http.response.header").items():
        span.set_attribute(key, value)


# ---------------------------------------------------------------------------
# Provider lifecycle
# ---------------------------------------------------------------------------


def reset_tracing() -> None:
    """Reset tracing state — for testing only.

    Clears OpenTelemetry's set-once global so a fresh `configure_tracing()`
    call in the next test takes effect. Touches OTel internals on purpose;
    if a future SDK release breaks the attribute names, fix it here in one place.
    """
    trace._TRACER_PROVIDER = None
    trace._TRACER_PROVIDER_SET_ONCE._done = False


def configure_tracing(
    service_name: str | None = None,
    config: TracingConfig | None = None,
) -> TracerProvider:
    """Set up the OTel TracerProvider.

    With ``config.exporter == "otlp"`` (the default) spans are exported via
    OTLP gRPC. With ``"none"`` we install a TracerProvider with no span
    processors so application code that obtains tracers/spans still works,
    but nothing tries to dial out — useful for local dev where the
    collector isn't running. Idempotent.
    """
    current = trace.get_tracer_provider()
    if isinstance(current, TracerProvider):
        return current

    cfg = config if config is not None else TracingConfig()
    resolved_name = service_name if service_name else os.getenv("OTEL_SERVICE_NAME", "jentic-one")
    resource = Resource.create({"service.name": resolved_name})
    provider = TracerProvider(resource=resource)
    if cfg.exporter == "otlp":
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    return provider


def current_trace_id() -> str | None:
    """Return the active span's trace id as 32 lowercase hex chars, or ``None``.

    Within a request handler this is the sanctioned way to obtain the request's
    trace id: the inbound instrumentation (``instrument_inbound_app``) has
    already extracted the W3C ``traceparent`` — or started a fresh trace when
    the header was absent — so the active span context carries exactly the id
    a caller would want to correlate on. Returns ``None`` when there is no
    valid span context (e.g. tracing was never configured), so callers must
    bring their own fallback.
    """
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return None
    return format(span_context.trace_id, "032x")


# ---------------------------------------------------------------------------
# Outbound (upstream) propagation
# ---------------------------------------------------------------------------


def instrument_outbound_client(client: httpx.AsyncClient) -> None:
    """Instrument the shared outbound ``httpx`` client for W3C propagation (§04).

    Distributed tracing must continue *into* the upstream: the outbound request
    carries the W3C ``traceparent``/``tracestate`` so a vendor running OTel can
    stitch its spans onto ours. The instrumentor injects those headers at
    request time, so they compose with — and never overwrite — injected
    credentials (distinct header names).

    Redaction (``request_hook``/``response_hook``) is wired here, in the single
    OTel home, so no instrumentation site can forget it: no bodies are captured
    and headers are recorded only via the ``_SAFE_SPAN_HEADERS`` safe-list.
    Per-client (not global) so it binds to the one shared pool the lifespan owns.
    """
    HTTPXClientInstrumentor().instrument_client(
        client,
        request_hook=_redact_request_span,
        response_hook=_redact_response_span,
    )


# Header names that must be scrubbed if any inbound capture is ever enabled. The
# instrumentor captures no headers by default; passing this sanitize-fields list
# is the belt-and-suspenders guarantee that a secret/PII header can never reach a
# span attribute even if a future config opts into header capture.
_INBOUND_SANITIZE_FIELDS: list[str] = [
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "api-key",
]


def instrument_inbound_app(app: object) -> None:
    """Instrument a FastAPI app for inbound (server-side) tracing (§04).

    Routed through the facade — not called inline with an ad-hoc
    ``opentelemetry`` import at the call site — so the redaction policy lives in
    one place. Captures **no** request/response headers (the default) and passes
    the secret/PII ``sanitize_fields`` list so that even if header capture is
    later enabled, ``authorization``/``cookie``/``x-api-key`` are scrubbed before
    they reach a span. Bodies are never captured.
    """
    FastAPIInstrumentor.instrument_app(
        app,  # type: ignore[arg-type]
        http_capture_headers_sanitize_fields=_INBOUND_SANITIZE_FIELDS,
    )


# ---------------------------------------------------------------------------
# jentic= tracestate vendor member
# ---------------------------------------------------------------------------

# The ``jentic`` vendor ``tracestate`` key and its value format, kept in one
# place with a contract test against the OpenAPI example
# (``openapi/broker/broker.openapi.yaml`` ``Tracestate``):
#   jentic=<exec_id>:<toolkit_id>:<vendor>:<name>:<version>
# The first two segments answer *who is calling*; the trailing three answer
# *what is being called*. ``HTTPXClientInstrumentor`` only propagates the
# standard W3C members, so the broker packs this one itself.
JENTIC_TRACESTATE_KEY = "jentic"
_TRACESTATE_PLACEHOLDER = "_"


def pack_jentic_tracestate(
    *,
    execution_id: str,
    toolkit_id: str | None,
    vendor: str | None,
    name: str | None,
    version: str | None,
) -> str:
    """Pack the ``jentic=`` ``tracestate`` member value.

    Missing segments are emitted as ``_`` rather than dropped so the value keeps
    a fixed five-field shape (``exec:tk:vendor:name:version``) a consumer can
    split positionally.
    """

    def _seg(value: str | None) -> str:
        return value if value else _TRACESTATE_PLACEHOLDER

    return ":".join(
        (
            _seg(execution_id),
            _seg(toolkit_id),
            _seg(vendor),
            _seg(name),
            _seg(version),
        )
    )


@contextmanager
def jentic_tracestate(member_value: str) -> Iterator[None]:
    """Activate the ``jentic`` ``tracestate`` member for the enclosed block.

    Mutates the active span context's ``TraceState`` — composing with any
    existing vendor entries rather than overwriting them — and attaches it as
    the current context so the W3C propagator serializes it onto the outbound
    ``tracestate`` for any request dispatched inside the block. Restores the
    prior context on exit. A no-op (yields unchanged) when there is no valid
    span context (e.g. ``exporter = none`` / outside a trace), so credential
    injection and dispatch still run.
    """
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        yield
        return
    updated = span_context.trace_state.add(JENTIC_TRACESTATE_KEY, member_value)
    new_context = trace.set_span_in_context(
        trace.NonRecordingSpan(
            trace.SpanContext(
                trace_id=span_context.trace_id,
                span_id=span_context.span_id,
                is_remote=span_context.is_remote,
                trace_flags=span_context.trace_flags,
                trace_state=updated,
            )
        )
    )
    token = otel_context.attach(new_context)
    try:
        yield
    finally:
        otel_context.detach(token)
