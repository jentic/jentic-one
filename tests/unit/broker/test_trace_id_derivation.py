"""Unit tests for broker trace-id derivation at the execute edge (#903).

Before the fix, ``_context_from_discovery`` used the **raw** ``traceparent``
header value (never a valid trace id) and defaulted to the literal
``"unknown"`` — both of which crashed ``emit_event`` (``ValueError``) inside
credential injection and 500'd the whole execute request.
"""

from __future__ import annotations

import re

import pytest
from opentelemetry import context as otel_context
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    TraceFlags,
    set_span_in_context,
)
from starlette.datastructures import Headers

from jentic_one.broker.web.routers.execute import _derive_trace_id, _parse_traceparent

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
# The exact reproduction header from issue #903 (a trace id, not a secret).
_ISSUE_TRACEPARENT = "00-801384a0e0eb70d0c180cd38bcba4d38-9baaee8f1fb43393-01"
_ISSUE_TRACE_ID = "801384a0e0eb70d0c180cd38bcba4d38"  # pragma: allowlist secret


def test_traceparent_trace_id_field_is_extracted() -> None:
    """The 32-hex trace-id field is parsed out — never the raw header value."""
    derived = _derive_trace_id(Headers({"traceparent": _ISSUE_TRACEPARENT}))
    assert derived == _ISSUE_TRACE_ID


def test_no_trace_headers_mints_a_valid_random_id() -> None:
    """Absent trace headers mint a fresh 32-hex id, never the literal 'unknown'."""
    first = _derive_trace_id(Headers({}))
    second = _derive_trace_id(Headers({}))
    assert _HEX32.match(first)
    assert _HEX32.match(second)
    assert first != second


@pytest.mark.parametrize(
    "malformed",
    [
        "",
        "garbage",
        "00-zz1384a0e0eb70d0c180cd38bcba4d38-9baaee8f1fb43393-01",  # non-hex trace id
        "00-801384a0e0eb70d0c180cd38bcba4d3-9baaee8f1fb43393-01",  # 31 chars
        "00-" + "0" * 32 + "-9baaee8f1fb43393-01",  # all-zeros is invalid per W3C
        "00-801384a0e0eb70d0c180cd38bcba4d38",  # too few fields
    ],
)
def test_malformed_traceparent_falls_back_to_minting(malformed: str) -> None:
    derived = _derive_trace_id(Headers({"traceparent": malformed}))
    assert _HEX32.match(derived)


def test_parse_traceparent_accepts_uppercase_trace_id() -> None:
    """Hex digits are case-insensitive on the wire; the id is normalised to lower."""
    value = "00-801384A0E0EB70D0C180CD38BCBA4D38-9baaee8f1fb43393-01"
    assert _parse_traceparent(value) == _ISSUE_TRACE_ID


def test_hex32_x_request_id_is_honoured() -> None:
    """The #903 workaround header works as a no-span fallback.

    On an instrumented surface the active span's id supersedes it (branch 1 of
    ``_derive_trace_id``) — a caller who needs to pick the trace id must send
    ``traceparent``. This exercises the uninstrumented (no active span) path.
    """
    derived = _derive_trace_id(Headers({"x-request-id": "AB" * 16}))
    assert derived == "ab" * 16


def test_all_zeros_x_request_id_falls_back_to_minting() -> None:
    """The all-zeros id is invalid per W3C — rejected on every branch."""
    derived = _derive_trace_id(Headers({"x-request-id": "0" * 32}))
    assert _HEX32.match(derived)
    assert derived != "0" * 32


def test_non_hex_x_request_id_falls_back_to_minting() -> None:
    derived = _derive_trace_id(Headers({"x-request-id": "req_abc123"}))
    assert _HEX32.match(derived)


def test_traceparent_takes_precedence_over_x_request_id() -> None:
    headers = Headers({"traceparent": _ISSUE_TRACEPARENT, "x-request-id": "cd" * 16})
    assert _derive_trace_id(headers) == _ISSUE_TRACE_ID


def test_active_otel_span_wins_over_headers() -> None:
    """Inside a request the instrumented span is the source of truth."""
    span_context = SpanContext(
        trace_id=0xDEADBEEFDEADBEEFDEADBEEFDEADBEEF,
        span_id=0x1234567812345678,
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    token = otel_context.attach(set_span_in_context(NonRecordingSpan(span_context)))
    try:
        derived = _derive_trace_id(Headers({"traceparent": _ISSUE_TRACEPARENT}))
    finally:
        otel_context.detach(token)
    assert derived == "deadbeefdeadbeefdeadbeefdeadbeef"
