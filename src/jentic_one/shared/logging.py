"""Structured logging configuration using structlog."""

from __future__ import annotations

import logging
import re
import sys
import uuid
from collections.abc import MutableMapping
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import httpx
import structlog
from opentelemetry import trace
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from jentic_one.shared.config import AppConfig
from jentic_one.shared.redaction import redact_event, redact_url_query

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")

_REQUEST_ID_MAX_LENGTH = 128
_REQUEST_ID_PATTERN = re.compile(r"^[a-zA-Z0-9\-_]+$")


def add_otel_context(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Inject trace_id and span_id from the active OpenTelemetry span."""
    span = trace.get_current_span()
    if span.is_recording():
        ctx = span.get_span_context()
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


class _OutboundUrlQueryFilter(logging.Filter):
    """Mask query-string values in httpx's ``HTTP Request: <METHOD> <url> …`` line.

    httpx logs every request at INFO with the full URL, and the broker appends
    ``location=query`` API keys to the upstream query string. The URL argument
    is rewritten via :func:`redact_url_query` (host and path kept) before any
    handler formats the record. Never drops a record.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and args:
            record.args = tuple(
                redact_url_query(str(arg)) if isinstance(arg, httpx.URL) else arg for arg in args
            )
        return True


# Third-party loggers whose DEBUG output is raw outbound wire detail (see
# ``configure_logging``). Gated together by ``logging.http_wire_trace``.
_WIRE_TRACE_LOGGERS: tuple[str, ...] = ("httpcore", "hpack")


def _install_httpx_url_filter() -> None:
    httpx_logger = logging.getLogger("httpx")
    if not any(isinstance(f, _OutboundUrlQueryFilter) for f in httpx_logger.filters):
        httpx_logger.addFilter(_OutboundUrlQueryFilter())


def _build_file_handler(config: AppConfig) -> RotatingFileHandler:
    """Build a rotating file handler that always writes JSON, regardless of debug.

    The file sink is parseable by design, so it ignores the console-vs-JSON
    toggle used for stdout and always emits one JSON object per line.
    """
    log_dir = Path(config.logging.file_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    file_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            redact_event,
            structlog.processors.JSONRenderer(),
        ],
    )

    file_handler = RotatingFileHandler(
        log_dir / config.logging.file_name,
        maxBytes=config.logging.file_max_bytes,
        backupCount=config.logging.file_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(file_formatter)
    return file_handler


def configure_logging(config: AppConfig) -> None:
    """Configure structlog and stdlib logging from application config."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            add_otel_context,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer: structlog.types.Processor = (
        structlog.dev.ConsoleRenderer()
        if config.runtime.debug
        else structlog.processors.JSONRenderer()
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            redact_event,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)

    if config.logging.file_enabled:
        root.addHandler(_build_file_handler(config))

    root.setLevel(config.runtime.log_level)

    # aiosqlite emits a DEBUG line for every executed statement ("executing ...",
    # "operation ... completed"), which floods stdout when the app runs at DEBUG.
    # Clamp it to INFO so our own DEBUG logs stay readable.
    logging.getLogger("aiosqlite").setLevel(logging.INFO)

    # Outbound wire-level DEBUG loggers can quote injected credentials: httpcore's
    # trace lines ("send_request_headers.failed exception=…") repr the raw
    # transport exception (which can include an outbound header value), and
    # hpack (HTTP/2 header compression, on by default via ``broker.http2``) logs
    # every encoded header name/value, including ``:path`` with its query string.
    # Clamp them to INFO so those never reach a sink, unless an operator
    # explicitly opts into wire tracing for local debugging.
    wire_level = logging.NOTSET if config.logging.http_wire_trace else logging.INFO
    for name in _WIRE_TRACE_LOGGERS:
        logging.getLogger(name).setLevel(wire_level)

    # httpx's INFO request line carries the full outbound URL — mask its query
    # values (query-located API keys) before any handler formats it.
    _install_httpx_url_filter()


def _is_valid_request_id(value: str) -> bool:
    """Check that a request ID is safe to propagate."""
    return len(value) <= _REQUEST_ID_MAX_LENGTH and _REQUEST_ID_PATTERN.match(value) is not None


def _read_request_id(scope: Scope) -> str:
    """Derive a request ID from the inbound ``x-request-id`` header or mint one.

    Reads raw ASGI headers off ``scope`` rather than constructing a Starlette
    ``Request``, keeping the hot path allocation-light.
    """
    headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
    for name, value in headers:
        if name == b"x-request-id":
            incoming = value.decode("latin-1")
            if _is_valid_request_id(incoming):
                return incoming
            break
    return f"req_{uuid.uuid4().hex[:16]}"


def _request_id_send(send: Send, req_id: str) -> Send:
    """Wrap ``send`` so ``http.response.start`` carries the ``x-request-id`` header.

    Stamping the header at the ASGI layer (mirroring the broker's
    ``_drain_close_send``) avoids materialising a Starlette ``Response``, so the
    middleware never wraps streaming responses in a cancel scope — the root
    cause of the SSE-disconnect connection leak in #627.
    """
    header = req_id.encode("latin-1")

    async def wrapped(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = [
                (name, value)
                for name, value in message.get("headers", [])
                if name.lower() != b"x-request-id"
            ]
            headers.append((b"x-request-id", header))
            message = {**message, "headers": headers}
        await send(message)

    return wrapped


class RequestIDMiddleware:
    """Assign or propagate a request ID on every HTTP request.

    Implemented as **pure ASGI middleware** (not ``BaseHTTPMiddleware``).
    ``BaseHTTPMiddleware`` wraps the downstream app in an anyio task + cancel
    scope; on SSE client disconnect that cancellation can land while a pooled
    asyncpg connection is mid-acquire inside SQLAlchemy, stranding the
    connection (#627). Operating directly over ``scope``/``receive``/``send``
    sidesteps that interaction entirely.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        req_id = _read_request_id(scope)
        request_id_ctx.set(req_id)
        structlog.contextvars.bind_contextvars(request_id=req_id)
        try:
            await self._app(scope, receive, _request_id_send(send, req_id))
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
