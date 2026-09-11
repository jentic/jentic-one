"""Smoke tests: broker forwards each harness domain operation unmodified.

Each op is proxied through the broker; the assertion is that the harness's own
behaviour survives the round trip (modulo the ``Jentic-*`` headers the broker
adds). Covers echo/no-mutation, pagination (cursor/link/offset), spec-vs-reality
drift, transport edge cases, parameter serialization, query-array limits,
lifecycle headers, and server-URL resolution.

All ops use the ``executable_harness`` fixture: the broker resolves a credential
for *every* proxied op, so a directly bound, allow-all credential is the
minimum wiring even for unsecured ops.

Open items resolved in-code:
- broker retry-on-503: none in the proxy path (see resilience module).
- broker body cap: ``/edge/chunked`` (1 MiB) is under the 10 MiB
  ``max_response_bytes`` default, so full relay is expected.
"""

from __future__ import annotations

import json
import uuid
from urllib.parse import urlparse

import pytest

from tests.harness.smoke_upstream.routers.edge import EDGE_CHUNKED_BYTES
from tests.smoke.conftest import (
    ExecutableHarness,
    _skip_if_no_admin_surface,
    broker_call,
)

# ---------------------------------------------------------------------------
# Echo / no-mutation
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_echo_forwards_body_and_headers(
    broker_url: str,
    executable_harness: ExecutableHarness,
    upstream_incluster_url: str,
) -> None:
    """POST /behavior/echo round-trips a body + custom header without mutation.

    Asserts the broker forwarded the exact body, did not strip the custom header,
    and injected the bound bearer credential as ``Authorization: Bearer …``.
    """
    _skip_if_no_admin_surface()
    payload = f"smoke-echo-{uuid.uuid4().hex}".encode()
    raw, status, _ = broker_call(
        broker_url,
        f"{upstream_incluster_url}/behavior/echo",
        method="POST",
        token=executable_harness.agent_token,
        headers={"X-Smoke-Custom": "kept", "Content-Type": "application/octet-stream"},
        body=payload,
    )
    assert status == 200, f"{status}: {raw!r}"
    echo = json.loads(raw)
    assert echo["method"] == "POST"
    assert echo["url_path"] == "/behavior/echo"
    assert echo["body_text"] == payload.decode()
    # Header names are lower-cased by Starlette's dict(request.headers).
    assert echo["headers"].get("x-smoke-custom") == "kept"
    auth = echo["headers"].get("authorization", "")
    assert auth.lower().startswith("bearer "), f"injected bearer missing: {auth!r}"
    assert executable_harness.bearer_token in auth


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_pagination_cursor(
    broker_url: str,
    executable_harness: ExecutableHarness,
    upstream_incluster_url: str,
) -> None:
    """/pagination/cursor returns a 10-item page + next_cursor; the next page differs."""
    _skip_if_no_admin_surface()
    raw, status, _ = broker_call(
        broker_url,
        f"{upstream_incluster_url}/pagination/cursor",
        token=executable_harness.agent_token,
    )
    assert status == 200, f"{status}: {raw!r}"
    page1 = json.loads(raw)
    assert len(page1["data"]) == 10
    assert page1["next_cursor"]

    raw2, status2, _ = broker_call(
        broker_url,
        f"{upstream_incluster_url}/pagination/cursor?cursor={page1['next_cursor']}",
        token=executable_harness.agent_token,
    )
    assert status2 == 200, f"{status2}: {raw2!r}"
    page2 = json.loads(raw2)
    assert page2["data"] != page1["data"]


@pytest.mark.smoke
def test_pagination_link_header(
    broker_url: str,
    executable_harness: ExecutableHarness,
    upstream_incluster_url: str,
) -> None:
    """/pagination/links → the broker forwards the RFC 8288 Link response header."""
    _skip_if_no_admin_surface()
    raw, status, headers = broker_call(
        broker_url,
        f"{upstream_incluster_url}/pagination/links",
        token=executable_harness.agent_token,
    )
    assert status == 200, f"{status}: {raw!r}"
    # Header lookup is case-insensitive via the response headers dict.
    link = next((v for k, v in headers.items() if k.lower() == "link"), "")
    assert 'rel="next"' in link, f"Link header not forwarded: {headers}"


@pytest.mark.smoke
def test_pagination_offset(
    broker_url: str,
    executable_harness: ExecutableHarness,
    upstream_incluster_url: str,
) -> None:
    """/pagination/offset?limit=5&offset=20 → 5-item page, total 25."""
    _skip_if_no_admin_surface()
    raw, status, _ = broker_call(
        broker_url,
        f"{upstream_incluster_url}/pagination/offset?limit=5&offset=20",
        token=executable_harness.agent_token,
    )
    assert status == 200, f"{status}: {raw!r}"
    body = json.loads(raw)
    assert len(body["data"]) == 5
    assert body["total"] == 25


# ---------------------------------------------------------------------------
# Drift (spec vs reality)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_drift_status_code(
    broker_url: str,
    executable_harness: ExecutableHarness,
    upstream_incluster_url: str,
) -> None:
    """/drift/status-code → the broker passes the undocumented 418 through."""
    _skip_if_no_admin_surface()
    raw, status, _ = broker_call(
        broker_url,
        f"{upstream_incluster_url}/drift/status-code",
        token=executable_harness.agent_token,
    )
    assert status == 418, f"expected undocumented 418 passthrough, got {status}: {raw!r}"


@pytest.mark.smoke
def test_drift_content_type(
    broker_url: str,
    executable_harness: ExecutableHarness,
    upstream_incluster_url: str,
) -> None:
    """/drift/content-type → the broker forwards the plain-text body intact."""
    _skip_if_no_admin_surface()
    raw, status, _ = broker_call(
        broker_url,
        f"{upstream_incluster_url}/drift/content-type",
        token=executable_harness.agent_token,
    )
    assert status == 200, f"{status}: {raw!r}"
    assert raw == b"plain text where JSON was declared"


@pytest.mark.smoke
def test_drift_schema(
    broker_url: str,
    executable_harness: ExecutableHarness,
    upstream_incluster_url: str,
) -> None:
    """/drift/schema → the broker forwards the off-schema body intact."""
    _skip_if_no_admin_surface()
    raw, status, _ = broker_call(
        broker_url,
        f"{upstream_incluster_url}/drift/schema",
        token=executable_harness.agent_token,
    )
    assert status == 200, f"{status}: {raw!r}"
    assert json.loads(raw) == {"foo": "bar"}


# ---------------------------------------------------------------------------
# Transport edge cases
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_edge_binary_uncorrupted(
    broker_url: str,
    executable_harness: ExecutableHarness,
    upstream_incluster_url: str,
) -> None:
    """POST /edge/binary → the broker forwards the raw bytes uncorrupted.

    The harness returns sha256 + byte length; asserting on ``bytes`` proves the
    body length survived (and sha256 proves no mutation).
    """
    _skip_if_no_admin_surface()
    blob = bytes(range(256)) * 4  # 1024 bytes spanning every byte value
    raw, status, _ = broker_call(
        broker_url,
        f"{upstream_incluster_url}/edge/binary",
        method="POST",
        token=executable_harness.agent_token,
        headers={"Content-Type": "application/octet-stream"},
        body=blob,
    )
    assert status == 200, f"{status}: {raw!r}"
    body = json.loads(raw)
    assert body["bytes"] == len(blob)


@pytest.mark.smoke
def test_edge_chunked_full_relay(
    broker_url: str,
    executable_harness: ExecutableHarness,
    upstream_incluster_url: str,
) -> None:
    """POST /edge/chunked → the broker relays the full 1 MiB streamed body.

    1 MiB is under the default 10 MiB ``max_response_bytes`` cap, so full relay is
    expected. If a deployment lowers the cap below 1 MiB, this asserts the wrong
    thing — revisit alongside ``broker.upstream.max_response_bytes``.
    """
    _skip_if_no_admin_surface()
    raw, status, _ = broker_call(
        broker_url,
        f"{upstream_incluster_url}/edge/chunked",
        method="POST",
        token=executable_harness.agent_token,
        client_timeout=60.0,
    )
    assert status == 200, f"{status}: {len(raw)} bytes"
    assert len(raw) == EDGE_CHUNKED_BYTES


@pytest.mark.smoke
def test_edge_empty_204(
    broker_url: str,
    executable_harness: ExecutableHarness,
    upstream_incluster_url: str,
) -> None:
    """GET /edge/empty-204 → 204 with an empty body, passed through."""
    _skip_if_no_admin_surface()
    raw, status, _ = broker_call(
        broker_url,
        f"{upstream_incluster_url}/edge/empty-204",
        token=executable_harness.agent_token,
    )
    assert status == 204, f"{status}: {raw!r}"
    assert raw == b""


@pytest.mark.smoke
def test_edge_html_error(
    broker_url: str,
    executable_harness: ExecutableHarness,
    upstream_incluster_url: str,
) -> None:
    """GET /edge/html-error → the broker passes the 502 + HTML body through verbatim."""
    _skip_if_no_admin_surface()
    raw, status, _ = broker_call(
        broker_url,
        f"{upstream_incluster_url}/edge/html-error",
        token=executable_harness.agent_token,
    )
    assert status == 502, f"expected upstream 502 passthrough, got {status}"
    assert raw.startswith(b"<html>")


# ---------------------------------------------------------------------------
# Parameter serialization
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_parameters_pipe_delimited(
    broker_url: str,
    executable_harness: ExecutableHarness,
    upstream_incluster_url: str,
) -> None:
    """/parameters/query/pipe?array=1|2|3 → echoed {"array": ["1","2","3"]}.

    The harness splits only on ``|`` and echoes string values, so the broker must
    forward the pipe-delimited serialization byte-for-byte.
    """
    _skip_if_no_admin_surface()
    raw, status, _ = broker_call(
        broker_url,
        f"{upstream_incluster_url}/parameters/query/pipe?array=1|2|3",
        token=executable_harness.agent_token,
    )
    assert status == 200, f"{status}: {raw!r}"
    assert json.loads(raw) == {"array": ["1", "2", "3"]}


@pytest.mark.smoke
def test_parameters_deep_object(
    broker_url: str,
    executable_harness: ExecutableHarness,
    upstream_incluster_url: str,
) -> None:
    """/parameters/query/deep-object?user[name]=x → echoed {"user": {"name": "x"}}."""
    _skip_if_no_admin_surface()
    raw, status, _ = broker_call(
        broker_url,
        f"{upstream_incluster_url}/parameters/query/deep-object?user[name]=x",
        token=executable_harness.agent_token,
    )
    assert status == 200, f"{status}: {raw!r}"
    assert json.loads(raw) == {"user": {"name": "x"}}


# ---------------------------------------------------------------------------
# Query-array limits
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_limits_query_array(
    broker_url: str,
    executable_harness: ExecutableHarness,
    upstream_incluster_url: str,
) -> None:
    """/limits/query-array?ids=… → the broker forwards every repeated param.

    The harness imposes no cap; this asserts the broker forwarded the full set
    (the URL-length 414 ceiling is a separate broker-config concern, not asserted
    here since no ceiling is configured for the smoke deployment).
    """
    _skip_if_no_admin_surface()
    ids = list(range(20))
    query = "&".join(f"ids={i}" for i in ids)
    raw, status, _ = broker_call(
        broker_url,
        f"{upstream_incluster_url}/limits/query-array?{query}",
        token=executable_harness.agent_token,
    )
    assert status == 200, f"{status}: {raw!r}"
    body = json.loads(raw)
    assert body["count"] == len(ids)
    assert body["ids"] == [str(i) for i in ids]


# ---------------------------------------------------------------------------
# Lifecycle headers
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_lifecycle_headers_passthrough(
    broker_url: str,
    executable_harness: ExecutableHarness,
    upstream_incluster_url: str,
) -> None:
    """/lifecycle/deprecated-endpoint → broker forwards Deprecation + Sunset verbatim."""
    _skip_if_no_admin_surface()
    raw, status, headers = broker_call(
        broker_url,
        f"{upstream_incluster_url}/lifecycle/deprecated-endpoint",
        token=executable_harness.agent_token,
    )
    assert status == 200, f"{status}: {raw!r}"
    assert json.loads(raw) == {"ok": True}
    lower = {k.lower(): v for k, v in headers.items()}
    assert lower.get("deprecation") == "true", headers
    assert lower.get("sunset") == "Wed, 11 Nov 2026 23:59:59 GMT", headers


# ---------------------------------------------------------------------------
# Server-URL resolution
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_servers_resolution(
    broker_url: str,
    executable_harness: ExecutableHarness,
    upstream_incluster_url: str,
) -> None:
    """/servers/resolution → echoed host is the harness DNS (no port), path intact.

    The harness reports ``request.url.hostname`` (host only, no ``:8084``),
    proving the broker resolved ``servers[].url`` to the harness and forwarded the
    request there.
    """
    _skip_if_no_admin_surface()
    raw, status, _ = broker_call(
        broker_url,
        f"{upstream_incluster_url}/servers/resolution",
        token=executable_harness.agent_token,
    )
    assert status == 200, f"{status}: {raw!r}"
    expected_host = urlparse(upstream_incluster_url).hostname
    assert json.loads(raw) == {
        "host": expected_host,
        "path": "/servers/resolution",
        "resolved": True,
    }
