"""Unit tests for request/response header filtering."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from jentic_one.broker.core.proxy_headers import (
    PASSTHROUGH_CACHE_CONTROL,
    forward_headers,
    passthrough_response_headers,
    passthrough_streaming_headers,
    replay_headers,
)
from jentic_one.shared.web.cache_control import PRIVATE_NO_STORE


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


def test_agent_reason_header_not_forwarded_upstream() -> None:
    """The agent's ``Jentic-Reason`` is for the reviewer only; it never reaches the upstream."""
    out = forward_headers({"Jentic-Reason": "Customer asked for a refund", "X-Keep": "1"}, {})
    assert "jentic-reason" not in {k.lower() for k in out}
    assert out["X-Keep"] == "1"


def test_agent_reason_header_not_kept_for_a_held_or_queued_run() -> None:
    """Nor does it ride in the payload an approved call replays when it finally runs."""
    kept = replay_headers({"Jentic-Reason": "Customer asked", "Content-Type": "application/json"})
    assert kept == {"content-type": "application/json"}


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


@pytest.mark.parametrize("name", ["Content-Length", "content-length"])
def test_inbound_content_length_not_forwarded(name: str) -> None:
    """The caller's ``Content-Length`` describes the inbound body, never the outbound one.

    httpx frames the body the broker actually sends (none at all for a GET), so
    a forwarded length would be stale.
    """
    out = forward_headers({name: "7", "Content-Type": "application/json"}, {})
    assert "content-length" not in {k.lower() for k in out}
    assert out["Content-Type"] == "application/json"


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


@pytest.mark.parametrize(
    "filter_headers", [passthrough_response_headers, passthrough_streaming_headers]
)
@pytest.mark.parametrize(
    "upstream_cache",
    [
        {"Cache-Control": "public, max-age=3600"},
        {"cache-control": "public, max-age=3600"},
        {},
    ],
)
def test_passthrough_cache_control_is_private_no_store(
    filter_headers: Callable[[dict[str, str]], dict[str, str]],
    upstream_cache: dict[str, str],
) -> None:
    """Pins #1835: a passthrough body was produced with a credential the broker
    injected, so whatever the upstream sent, it leaves as ``private, no-store``
    (exactly one value), while ``ETag`` still passes through."""
    out = filter_headers({"content-type": "text/csv", "etag": '"abc"', **upstream_cache})
    cache = [value for key, value in out.items() if key.lower() == "cache-control"]
    assert cache == [PASSTHROUGH_CACHE_CONTROL] == ["private, no-store"]
    assert out["etag"] == '"abc"'
    assert out["content-type"] == "text/csv"


def test_passthrough_cache_control_matches_control_plane_directive() -> None:
    assert PASSTHROUGH_CACHE_CONTROL == PRIVATE_NO_STORE
