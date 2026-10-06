"""OpenTelemetry tracing — the single OTel *tracing* home (facade).

Like ``shared/metrics.py`` for metrics, this module is the **only** sanctioned
place to import the OTel tracing SDK + instrumentation. No source module outside
this file may import ``opentelemetry.instrumentation.*`` or the OTel propagator
APIs directly — enforced by ``tests/arch/test_tracing_facade.py``. Centralising
the instrumentation here means the **redaction hooks can't be forgotten** by a
new call site (a compliance requirement — the broker proxies ``Authorization``,
injected API keys, cookies, and arbitrary tenant bodies that may carry PII).

It owns four concerns:

1. ``configure_tracing`` — the global ``TracerProvider`` (OTLP gRPC or no-op).
   Every exporter it wires is wrapped in :class:`ScrubbingSpanExporter`, which
   removes free-text exception detail, query-string values and non-safe-listed
   captured headers from every span before it leaves the process.
2. ``instrument_outbound_client`` — W3C ``traceparent``/``tracestate`` propagation
   *into* the upstream over the shared ``httpx`` client, with span-attribute
   redaction (no bodies; headers via a safe-list only).
3. ``pack_jentic_tracestate`` / ``jentic_tracestate`` — the ``jentic=``
   vendor ``tracestate`` member (``exec:tk:vendor:name:version``) the broker must
   pack itself (the instrumentor only propagates standard W3C keys).
"""

from __future__ import annotations

import functools
import linecache
import os
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING

import httpx
import structlog
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.attributes import BoundedAttributes
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.util import BoundedList
from opentelemetry.trace import Status

from jentic_one.shared.config import TracingConfig
from jentic_one.shared.redaction import redact_query_string, redact_url_query

if TYPE_CHECKING:
    from opentelemetry.instrumentation.httpx import RequestInfo, ResponseInfo
    from opentelemetry.trace import Span

# A span attribute value. ``opentelemetry.util.types.AttributeValue`` stopped
# being a valid type for mypy in 1.45 (it is now a chained assignment), so the
# primitive and homogeneous-sequence shapes spans accept are spelled out here.
type AttributeValue = (
    str | bool | int | float | Sequence[str] | Sequence[bool] | Sequence[int] | Sequence[float]
)

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
    masks the query values in that recorded URL, never adds the request body,
    and only mirrors safe-listed headers, so a proxied ``Authorization`` /
    injected API key (header or query) / cookie never lands on a span.
    """
    if not span.is_recording():
        return
    _mask_url_attributes(span, info)
    if info.headers is None:
        return
    for key, value in _safe_header_attributes(info.headers, prefix="http.request.header").items():
        span.set_attribute(key, value)


# The instrumentor's URL attributes (legacy ``http.url``, stable ``url.full``).
# Its own ``redact_url`` only masks a few AWS/GCS signature parameters, so a
# ``location=query`` API key would otherwise be recorded verbatim.
_URL_SPAN_ATTRIBUTES: tuple[str, ...] = ("http.url", "url.full")


def _mask_url_attributes(span: Span, info: RequestInfo) -> None:
    """Overwrite the recorded request URL with its query values masked."""
    recorded = getattr(span, "attributes", None) or {}
    masked = redact_url_query(str(info.url))
    for key in _URL_SPAN_ATTRIBUTES:
        if key in recorded:
            span.set_attribute(key, masked)


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


async def _redact_request_span_async(span: Span, info: RequestInfo) -> None:
    """Async twin of :func:`_redact_request_span` for ``httpx.AsyncClient``.

    ``instrument_client`` only invokes a request hook on an async client when
    the hook is a coroutine function — a plain function is silently ignored.
    """
    _redact_request_span(span, info)


async def _redact_response_span_async(span: Span, request: RequestInfo, info: ResponseInfo) -> None:
    """Async twin of :func:`_redact_response_span` (see above)."""
    _redact_response_span(span, request, info)


# ---------------------------------------------------------------------------
# Export-time span scrubbing
# ---------------------------------------------------------------------------

# When a request fails, the SDK records the exception on every span it unwinds
# through (``use_span`` / ``Span.__exit__``): an ``exception`` event carrying
# ``str(exc)`` and a full formatted traceback, plus an ERROR status whose
# description is ``"<Type>: <str(exc)>"``. The outbound httpx instrumentor
# re-raises inside its client span, so the same text lands on that span *and*
# on every parent (broker runner/execute spans, the inbound server span).
# httpx/httpcore/h11/ssl/proxy/auth-flow exception text can quote request
# material — ``Illegal header value b'<header value>'``, ``HTTPStatusError``'s
# ``for url '<full url with query>'``, ``InvalidURL`` — and a chained
# ``raise ... from exc`` repeats it inside the traceback. None of that may reach
# an exporter, so every exported span is rebuilt with that text removed.

_EXCEPTION_EVENT = "exception"
_EXCEPTION_TYPE = "exception.type"
_EXCEPTION_MESSAGE = "exception.message"
_EXCEPTION_STACKTRACE = "exception.stacktrace"
_EXCEPTION_ESCAPED = "exception.escaped"

# Fixed replacement for any exception message / status description: it carries
# no request data. The exception *type* is kept alongside it for diagnostics.
EXCEPTION_MESSAGE_PLACEHOLDER = "exception message omitted from exported telemetry"

# The only traceback lines kept: the fixed CPython framing lines and the
# ``File "<path>", line <n>, in <func>`` frame locations. Source-code lines,
# exception-message lines, ``__notes__`` and exception-group rendering are all
# dropped — any of them can carry the message text.
_TRACEBACK_FIXED_LINES: frozenset[str] = frozenset(
    {
        "Traceback (most recent call last):",
        "The above exception was the direct cause of the following exception:",
        "During handling of the above exception, another exception occurred:",
    }
)
_TRACEBACK_FRAME_LINE = re.compile(
    r'^  File "(?P<path>[^"\r\n]+\.py)", line (?P<lineno>[1-9]\d{0,6}), '
    r"in (?P<func>[A-Za-z_<][\w<>.]*)$"
)
# Code-object names CPython gives code that has no ``def``/``class`` statement.
_SYNTHETIC_FRAME_NAMES: frozenset[str] = frozenset(
    {"<module>", "<lambda>", "<genexpr>", "<listcomp>", "<dictcomp>", "<setcomp>"}
)

# Attributes whose value is a URL / query string: recorded by the outbound
# httpx instrumentor (``http.url``/``url.full``) and by the inbound ASGI one
# (``http.url`` with the *decoded* query string, ``http.target``,
# ``url.query``). Query values are masked on every span; names, host and path
# are kept (``http.route`` holds the route template and is left alone).
_QUERY_STRING_ATTRIBUTES: frozenset[str] = frozenset({"url.query"})
_URL_BEARING_ATTRIBUTES: frozenset[str] = frozenset({*_URL_SPAN_ATTRIBUTES, "http.target"})

_HEADER_PREFIXES: tuple[str, ...] = ("http.request.header.", "http.response.header.")

_logger = structlog.get_logger(__name__)


@functools.lru_cache(maxsize=4096)
def _is_source_frame(path: str, lineno: int, func: str) -> bool:
    """Whether ``File "<path>", line <lineno>, in <func>`` names real source.

    A frame line is only trusted if it points at an existing ``.py`` file, the
    line is inside it, and the function name is defined there. Anything else is
    text that merely has the frame shape — e.g. a multi-line exception message
    — and could carry arbitrary data in the path or the name.
    """
    if not os.path.isfile(path):
        return False
    lines = linecache.getlines(path)
    if lineno > len(lines):
        return False
    name = func.rsplit(".", 1)[-1]
    if name.startswith("<"):
        return name in _SYNTHETIC_FRAME_NAMES
    if not name.isidentifier():
        return False
    pattern = re.compile(rf"\b(?:def|class)\s+{re.escape(name)}\b")
    return any(pattern.search(line) for line in lines)


def _frames_only(stacktrace: str) -> str:
    """Reduce a formatted traceback to verified frame locations (no message text)."""
    kept: list[str] = []
    for line in stacktrace.splitlines():
        if line in _TRACEBACK_FIXED_LINES:
            kept.append(line)
            continue
        match = _TRACEBACK_FRAME_LINE.match(line)
        if match and _is_source_frame(match["path"], int(match["lineno"]), match["func"]):
            kept.append(line)
    return "\n".join(kept)


def _scrub_exception_attributes(
    attributes: Mapping[str, AttributeValue],
) -> dict[str, AttributeValue]:
    """Keep ``exception.type``/``escaped``; replace the message; frames-only trace.

    Any other attribute a caller attached via ``record_exception(attributes=…)``
    is dropped — it is free-form and cannot be vetted here.
    """
    out: dict[str, AttributeValue] = {}
    if _EXCEPTION_TYPE in attributes:
        out[_EXCEPTION_TYPE] = attributes[_EXCEPTION_TYPE]
    out[_EXCEPTION_MESSAGE] = EXCEPTION_MESSAGE_PLACEHOLDER
    stacktrace = attributes.get(_EXCEPTION_STACKTRACE)
    if isinstance(stacktrace, str):
        out[_EXCEPTION_STACKTRACE] = _frames_only(stacktrace)
    if _EXCEPTION_ESCAPED in attributes:
        out[_EXCEPTION_ESCAPED] = attributes[_EXCEPTION_ESCAPED]
    return out


def _scrub_attribute(key: str, value: AttributeValue) -> AttributeValue | None:
    """Filter one span attribute; ``None`` means drop it.

    Masks query-string values in URL-bearing attributes (outbound *and* inbound
    spans — an inbound OAuth callback or device-verification URL carries
    ``code``/``state`` in its query) and limits captured headers to
    ``_SAFE_SPAN_HEADERS``. Backstops the outbound request/response hooks for
    spans they never saw and for header capture switched on via
    ``OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_{CLIENT,SERVER}_*``, which the
    instrumentors record under ``http.{request,response}.header.<name>`` with
    only their own (operator-configured) sanitize list applied.
    """
    if key in _URL_BEARING_ATTRIBUTES:
        return redact_url_query(value) if isinstance(value, str) else None
    if key in _QUERY_STRING_ATTRIBUTES:
        return redact_query_string(value) if isinstance(value, str) else None
    for prefix in _HEADER_PREFIXES:
        if key.startswith(prefix):
            header = key[len(prefix) :].replace("_", "-").lower()
            return value if header in _SAFE_SPAN_HEADERS else None
    return value


def _needs_attribute_scrub(key: str) -> bool:
    return (
        key in _URL_BEARING_ATTRIBUTES
        or key in _QUERY_STRING_ATTRIBUTES
        or key.startswith(_HEADER_PREFIXES)
    )


def _bounded(attributes: Mapping[str, AttributeValue], dropped: int) -> BoundedAttributes:
    """Immutable attribute mapping that keeps the original dropped-count."""
    bounded = BoundedAttributes(maxlen=None, attributes=attributes, immutable=True)
    bounded.dropped = dropped
    return bounded


def _bounded_list[T](items: Sequence[T], dropped: int) -> BoundedList[T]:
    """Sequence that keeps the original dropped-count (read by exporters)."""
    bounded: BoundedList[T] = BoundedList.from_seq(None, items)
    bounded.dropped = dropped
    return bounded


def _scrub_events(span: ReadableSpan) -> tuple[list[Event], str | None]:
    """Rebuild the span's events with exception detail removed.

    Returns the new events and the type of the last exception event (used to
    rebuild the status description).
    """
    events: list[Event] = []
    last_type: str | None = None
    for event in span.events:
        if event.name != _EXCEPTION_EVENT:
            events.append(event)
            continue
        attrs = _scrub_exception_attributes(event.attributes or {})
        exc_type = attrs.get(_EXCEPTION_TYPE)
        if isinstance(exc_type, str):
            last_type = exc_type
        events.append(
            Event(
                event.name,
                attributes=_bounded(attrs, event.dropped_attributes),
                timestamp=event.timestamp,
            )
        )
    return events, last_type


def scrub_span(span: ReadableSpan) -> ReadableSpan:
    """Return ``span`` with free-text error detail and request data removed.

    Applied to **every** span, not only outbound client spans: the SDK re-records
    a propagating exception on each parent it unwinds through, so an outbound
    failure's text would otherwise reach the broker and inbound server spans
    too. Structural diagnostics survive everywhere — ``exception.type``,
    ``error.type``, status code, ``http.*`` status attributes and the traceback's
    frame locations; the message itself stays in the (redacted) logs, which
    correlate by ``trace_id``.

    The rebuilt span keeps everything an exporter serialises — context, parent,
    kind, resource, instrumentation scope, links, timestamps, status code and
    the dropped attribute/event/link counts. Spans with nothing to scrub are
    returned as-is (no copy).
    """
    has_exception = any(event.name == _EXCEPTION_EVENT for event in span.events)
    has_description = bool(span.status.description)
    source_attributes = span.attributes or {}
    scrub_attributes = any(_needs_attribute_scrub(key) for key in source_attributes)
    if not (has_exception or has_description or scrub_attributes):
        return span

    events: Sequence[Event] = span.events
    last_type: str | None = None
    if has_exception:
        events, last_type = _scrub_events(span)

    status = span.status
    if has_description:
        status = Status(
            span.status.status_code,
            description=last_type if last_type else EXCEPTION_MESSAGE_PLACEHOLDER,
        )

    attributes: Mapping[str, AttributeValue] = source_attributes
    if scrub_attributes:
        filtered: dict[str, AttributeValue] = {}
        for key, value in source_attributes.items():
            safe = _scrub_attribute(key, value)
            if safe is not None:
                filtered[key] = safe
        attributes = filtered

    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=_bounded(attributes, span.dropped_attributes),
        events=_bounded_list(events, span.dropped_events),
        links=_bounded_list(span.links, span.dropped_links),
        kind=span.kind,
        status=status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class ScrubbingSpanExporter(SpanExporter):
    """Exporter wrapper that hands the delegate only scrubbed spans.

    Scrubbing happens here — at the single point every span passes on its way
    out — rather than in a ``SpanProcessor``: ``on_end`` receives an immutable
    ``ReadableSpan`` snapshot, and ``on_start`` runs before any exception is
    recorded. Rebuilding the snapshot mirrors how the SDK itself produces one
    (``Span._readable_span``), so it relies only on the public constructor.

    A span whose scrub raises is dropped (never exported unscrubbed) and logged
    by name and error type; the rest of the batch is still exported. The
    wrapper holds no mutable state, so concurrent ``export`` calls are safe.
    """

    def __init__(self, delegate: SpanExporter) -> None:
        self._delegate = delegate

    @property
    def delegate(self) -> SpanExporter:
        return self._delegate

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        scrubbed: list[ReadableSpan] = []
        for span in spans:
            try:
                scrubbed.append(scrub_span(span))
            except Exception as exc:  # one bad span must not sink the batch
                _logger.warning(
                    "span_scrub_failed_span_dropped",
                    span_name=span.name,
                    error_type=type(exc).__qualname__,
                )
        if not scrubbed:
            return SpanExportResult.SUCCESS
        return self._delegate.export(scrubbed)

    def shutdown(self) -> None:
        self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._delegate.force_flush(timeout_millis)


def scrubbing_exporter(exporter: SpanExporter) -> ScrubbingSpanExporter:
    """Wrap ``exporter`` in :class:`ScrubbingSpanExporter` (idempotent)."""
    if isinstance(exporter, ScrubbingSpanExporter):
        return exporter
    return ScrubbingSpanExporter(exporter)


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
    *,
    exporter: SpanExporter | None = None,
) -> TracerProvider:
    """Set up the OTel TracerProvider.

    With ``config.exporter == "otlp"`` (the default) spans are exported via
    OTLP gRPC. With ``"none"`` we install a TracerProvider with no span
    processors so application code that obtains tracers/spans still works,
    but nothing tries to dial out — useful for local dev where the
    collector isn't running. Idempotent.

    ``exporter`` replaces the OTLP exporter (tests pass an in-memory one so
    they exercise the production chain). Whatever exporter is used, it is
    wrapped in :class:`ScrubbingSpanExporter` behind a ``BatchSpanProcessor``,
    so no span leaves the process with exception text or unsafe client
    attributes — for every surface and deploy mode, since they all build
    their provider here.
    """
    current = trace.get_tracer_provider()
    if isinstance(current, TracerProvider):
        return current

    cfg = config if config is not None else TracingConfig()
    resolved_name = service_name if service_name else os.getenv("OTEL_SERVICE_NAME", "jentic-one")
    resource = Resource.create({"service.name": resolved_name})
    provider = TracerProvider(resource=resource)
    if exporter is None and cfg.exporter == "otlp":
        exporter = OTLPSpanExporter()
    if exporter is not None:
        provider.add_span_processor(BatchSpanProcessor(scrubbing_exporter(exporter)))
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


def instrument_outbound_client(client: httpx.AsyncClient | httpx.Client) -> None:
    """Instrument the shared outbound ``httpx`` client for W3C propagation.

    Distributed tracing must continue *into* the upstream: the outbound request
    carries the W3C ``traceparent``/``tracestate`` so a vendor running OTel can
    stitch its spans onto ours. The instrumentor injects those headers at
    request time, so they compose with — and never overwrite — injected
    credentials (distinct header names).

    Redaction (``request_hook``/``response_hook``) is wired here, in the single
    OTel home, so no instrumentation site can forget it: no bodies are captured
    and headers are recorded only via the ``_SAFE_SPAN_HEADERS`` safe-list.
    Per-client (not global) so it binds to the one shared pool the lifespan owns.

    The hook flavour must match the client: the instrumentor silently drops a
    coroutine hook on a sync ``httpx.Client`` (and a plain hook on an
    ``AsyncClient``), which would leave the recorded URL unmasked.
    """
    if isinstance(client, httpx.AsyncClient):
        HTTPXClientInstrumentor().instrument_client(
            client,
            request_hook=_redact_request_span_async,
            response_hook=_redact_response_span_async,
        )
        return
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
    """Instrument a FastAPI app for inbound (server-side) tracing.

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
