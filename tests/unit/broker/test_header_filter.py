"""Unit tests for request/response header filtering."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from jentic_one.broker.core.headers import (
    connection_nominated_headers,
    scrub_sensitive_header_items,
)
from jentic_one.broker.core.proxy_headers import (
    PASSTHROUGH_CACHE_CONTROL,
    ResponseHeaderMap,
    forward_headers,
    passthrough_response_header_items,
    passthrough_response_headers,
    passthrough_streaming_header_items,
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


def test_response_passthrough_preserves_content_encoding_and_strips_content_length() -> None:
    # HttpRunner reads raw, still-compressed bytes via aiter_raw, so upstream
    # content-encoding must be preserved to describe RunnerResult.body accurately,
    # while content-length is dropped and recomputed by the ASGI server.
    out = passthrough_response_headers(
        {
            "content-type": "application/json",
            "content-encoding": "gzip",
            "content-length": "42",
            "etag": "abc",
        }
    )
    lowered = {k.lower() for k in out}
    assert out["content-encoding"] == "gzip"
    assert "content-length" not in lowered
    assert out["content-type"] == "application/json"
    assert out["etag"] == "abc"


def test_connection_nominated_hop_by_hop_stripped_from_requests() -> None:
    inbound = httpx.Headers(
        [
            ("Connection", "X-Remove-One, x-remove-two "),
            ("connection", "X-Auth-Hop, Keep-Alive"),
            ("X-Remove-One", "1"),
            ("X-Remove-Two", "2"),
            ("X-Auth-Hop", "secret"),
            ("X-Keep", "kept"),
        ]
    )
    out = forward_headers(inbound, {"X-Auth-Hop": "injected-after-strip"})
    lowered = {k.lower(): v for k, v in out.items()}
    assert "connection" not in lowered
    assert "x-remove-one" not in lowered
    assert "x-remove-two" not in lowered
    assert lowered["x-auth-hop"] == "injected-after-strip"
    assert lowered["x-keep"] == "kept"


def test_connection_nominated_hop_by_hop_stripped_from_replay_headers() -> None:
    inbound = httpx.Headers(
        [
            ("Connection", "Range, Notion-Version"),
            ("Range", "bytes=0-99"),
            ("Notion-Version", "2022-06-28"),
            ("Accept", "application/json"),
        ]
    )
    assert replay_headers(inbound) == {"accept": "application/json"}


def test_connection_nominated_hop_by_hop_stripped_from_responses() -> None:
    upstream = httpx.Headers(
        [
            ("Connection", "X-Internal-Hop, ETag"),
            ("connection", " x-second-hop "),
            ("X-Internal-Hop", " internal "),
            ("X-Second-Hop", "2"),
            ("ETag", '"abc"'),
            ("Content-Type", "application/json"),
        ]
    )
    assert connection_nominated_headers(upstream) == frozenset(
        {"x-internal-hop", "etag", "x-second-hop"}
    )
    for filter_fn in (passthrough_response_headers, passthrough_streaming_headers):
        out = filter_fn(upstream)
        lowered = {k.lower() for k in out}
        assert "connection" not in lowered
        assert "x-internal-hop" not in lowered
        assert "x-second-hop" not in lowered
        assert "etag" not in lowered
        assert out["content-type"] == "application/json"
        assert out["cache-control"] == PASSTHROUGH_CACHE_CONTROL


def test_repeated_response_headers_preserved_as_multi_items() -> None:
    cookie_1 = "a=1; Expires=Wed, 21 Oct 2026 07:28:00 GMT; Path=/"
    cookie_2 = "b=2; Path=/; HttpOnly"
    raw = [
        ("content-type", "application/json"),
        ("set-cookie", cookie_1),
        ("set-cookie", cookie_2),
        ("x-multi", "first"),
        ("x-multi", "second"),
        ("transfer-encoding", "chunked"),
    ]
    for item_fn in (passthrough_response_header_items, passthrough_streaming_header_items):
        items = item_fn(raw)
        cookies = [v for k, v in items if k.lower() == "set-cookie"]
        assert cookies == [cookie_1, cookie_2]
        multi = [v for k, v in items if k.lower() == "x-multi"]
        assert multi == ["first", "second"]
        assert all(k.lower() != "transfer-encoding" for k, _ in items)

    mapped = passthrough_response_headers(ResponseHeaderMap(raw, raw_headers=raw))
    assert mapped.getlist("set-cookie") == [cookie_1, cookie_2]
    assert mapped.getlist("x-multi") == ["first", "second"]


def test_scrub_sensitive_header_items_removes_cookies_and_keeps_repeated_fields() -> None:
    raw = [
        ("content-type", "application/json"),
        ("set-cookie", "a=1; Path=/"),
        ("set-cookie", "b=2; Path=/"),
        ("authorization", "Bearer secret"),
        ("vary", "Accept"),
        ("vary", "Accept-Encoding"),
        ("server-timing", "db;dur=2.1"),
    ]
    scrubbed = scrub_sensitive_header_items(raw, extra_excluded=("server-timing",))
    names = [k.lower() for k, _ in scrubbed]
    assert "set-cookie" not in names
    assert "authorization" not in names
    assert "server-timing" not in names
    assert [v for k, v in scrubbed if k.lower() == "vary"] == ["Accept", "Accept-Encoding"]


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
