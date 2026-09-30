"""Unit tests for request/response header filtering."""

from __future__ import annotations

from jentic_one.broker.core.proxy_headers import (
    forward_headers,
    passthrough_response_headers,
)


def test_hop_by_hop_stripped() -> None:
    out = forward_headers(
        {"Connection": "keep-alive", "TE": "trailers", "Accept": "application/json"},
        {},
    )
    assert "connection" not in {k.lower() for k in out}
    assert "te" not in {k.lower() for k in out}
    assert out["Accept"] == "application/json"


def test_host_not_forwarded() -> None:
    out = forward_headers({"Host": "broker.internal", "X-Keep": "1"}, {})
    assert "host" not in {k.lower() for k in out}
    assert out["X-Keep"] == "1"


def test_broker_consumed_stripped() -> None:
    out = forward_headers(
        {"Authorization": "Bearer caller", "Prefer": "respond-async", "X-Keep": "1"}, {}
    )
    assert "authorization" not in {k.lower() for k in out}
    assert "prefer" not in {k.lower() for k in out}
    assert out["X-Keep"] == "1"


def test_retired_toolkit_header_not_forwarded_upstream() -> None:
    """Old clients still send ``Jentic-Toolkit-Id``; it is ignored and never leaks."""
    out = forward_headers({"Jentic-Toolkit-Id": "tk_old", "X-Keep": "1"}, {})
    assert "jentic-toolkit-id" not in {k.lower() for k in out}
    assert out["X-Keep"] == "1"


def test_spoofable_forwarding_headers_stripped() -> None:
    out = forward_headers(
        {
            "X-Forwarded-For": "1.2.3.4",
            "Forwarded": "for=1.2.3.4",
            "Via": "1.1 proxy",
            "X-Real-IP": "1.2.3.4",
            "X-Keep": "1",
        },
        {},
    )
    lowered = {k.lower() for k in out}
    assert lowered == {"x-keep"}


def test_injected_wins_on_conflict() -> None:
    out = forward_headers({"X-Custom": "from-client"}, {"X-Custom": "injected"})
    assert out["X-Custom"] == "injected"


def test_response_passthrough_strips_hop_by_hop() -> None:
    out = passthrough_response_headers(
        {"content-type": "application/json", "transfer-encoding": "chunked", "etag": "abc"}
    )
    assert "transfer-encoding" not in {k.lower() for k in out}
    assert out["content-type"] == "application/json"
    assert out["etag"] == "abc"


def test_response_passthrough_strips_decoded_body_headers() -> None:
    # httpx decompresses the body, so the upstream content-encoding/content-length
    # no longer describe RunnerResult.body and must be dropped.
    out = passthrough_response_headers(
        {
            "content-type": "application/json",
            "content-encoding": "gzip",
            "content-length": "42",
            "etag": "abc",
        }
    )
    lowered = {k.lower() for k in out}
    assert "content-encoding" not in lowered
    assert "content-length" not in lowered
    assert out["content-type"] == "application/json"
    assert out["etag"] == "abc"
