"""The execute family for the mounted MCP app — control-plane→broker proxy.

The broker stays MCP-free, so the
mounted app forwards ``execute``/``execute_read`` calls server-side to the
broker configured at ``server.mcp.broker_url``, relaying the caller's own
bearer — the broker keeps enforcing identity, bindings, permission rules, and
credential injection exactly as if the agent had called it directly, and the
held-envelope (HELD/202) contract passes through untouched.

This is a faithful port of the Go stdio server's execute path
(``cli/internal/cli/api/mcp_execute.go`` over ``cli/internal/agentops``): the
same resolve → build → send → classify lifecycle, the same envelope keys
({schema_version, status, headers, body, execution_id} — shared goldens pin
them), the same context-protection caps (128 KiB body, 8 KiB headers), and the same coded
soft-error taxonomy (broker denial with the verbatim ``agent_directive``,
earned ``retryable`` hints on transport failures, SEC-1 refusal of a bearer
over plaintext to a non-loopback broker).
"""

from __future__ import annotations

import json
import uuid as uuid_mod
from importlib.metadata import PackageNotFoundError, version
from ipaddress import ip_address
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import httpx

from jentic_one.mcp.envelopes import (
    CODE_BROKER_DENIED,
    CODE_RESOLVE_FAILED,
    CODE_TRANSPORT_ERROR,
    SCHEMA_VERSION,
    ToolError,
)

#: Context-protection cap on a relayed response body (Go:
#: ``defaultMaxResultBytes``). MCP has no chunking — a tool result lands in
#: the model's context whole.
MAX_RESULT_BYTES = 128 << 10

#: Aggregate budget for relayed response headers (Go: ``headerBytesBudget``).
HEADER_BYTES_BUDGET = 8 << 10

#: The transport bound on the broker leg (Go: ``sdkclient.MaxBodyBytes``).
_MAX_BODY_BYTES = 64 << 20

#: The execute ceiling (Go: 60s client timeout on the broker leg).
_EXECUTE_TIMEOUT_SECONDS = 60.0

_LOOPBACK_HOSTS = frozenset({"localhost"})

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

_DENIAL_STATUSES = frozenset({401, 403, 409, 424})


def _is_loopback_host(hostname: str | None) -> bool:
    """Go's ``isLoopbackHost`` semantics (``cli/client/auth/oauth.go``):
    ``localhost`` or a literal loopback IP — parsed, never prefix-matched, so
    a public DNS name like ``127.0.0.1.evil.example`` never qualifies."""
    if hostname is None:
        return False
    name = hostname.lower()
    if name in _LOOPBACK_HOSTS:
        return True
    try:
        return ip_address(name).is_loopback
    except ValueError:
        return False


def resolve_broker_target(broker_url: str) -> tuple[str, str]:
    """Resolve the broker scheme/host from ``server.mcp.broker_url``.

    The two Go fail-closed guards port unchanged (``resolveMCPBrokerTarget``
    M2 + ``BuildRequest`` SEC-1): a malformed URL errors rather than silently
    dialing a default, and plaintext ``http`` is refused for a non-loopback
    host — the caller's bearer rides this hop.
    """
    parts = urlsplit(broker_url)
    if not parts.scheme or not parts.netloc:
        raise ToolError(
            CODE_RESOLVE_FAILED,
            f"server.mcp.broker_url is malformed ({broker_url!r}): "
            "it must be an absolute URL with a scheme and host",
            actionable="Ask your operator to set server.mcp.broker_url to the broker's "
            "base URL, e.g. https://<broker-host>:<port> (or http://127.0.0.1:8100 for "
            "a local install).",
        )
    if parts.scheme != "https" and not _is_loopback_host(parts.hostname):
        raise ToolError(
            CODE_TRANSPORT_ERROR,
            f"refusing to send the caller's bearer over plaintext to the non-loopback "
            f"broker {broker_url!r}",
            actionable="Use an https broker URL, or a loopback (127.0.0.1/localhost) "
            "http broker for local installs.",
        )
    return parts.scheme, parts.netloc


def parse_method_path(target: str) -> tuple[str, str]:
    """``METHOD:/path`` broker-relative targets (Go: ``agentops.ParseMethodPath``).

    Returns ``("", "")`` when the target is not in this form — an absolute
    ``METHOD:URL`` (``GET:https://…``) is deliberately not matched here.
    """
    idx = target.find(":")
    if idx < 1 or idx >= len(target) - 1 or target[idx + 1] != "/":
        return "", ""
    if idx + 2 < len(target) and target[idx + 2] == "/":
        return "", ""
    method = target[:idx].upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
        return "", ""
    return method, target[idx + 1 :]


def split_inputs(
    inputs: dict[str, Any] | None, target: str
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Fold the ``inputs`` object onto the path/query split (Go: ``splitInputs``).

    A key matching a ``{placeholder}`` in the resolved target is a path
    parameter; everything else is a query parameter. Sorted for determinism.
    """
    if not inputs:
        return [], []
    path_params: list[tuple[str, str]] = []
    query_params: list[tuple[str, str]] = []
    for key in sorted(inputs):
        value = _input_value_string(inputs[key])
        if "{" + key + "}" in target:
            path_params.append((key, value))
        else:
            query_params.append((key, value))
    return path_params, query_params


def _input_value_string(value: Any) -> str:
    """One inputs/headers value as the wire string (Go: ``inputValueString``)."""
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def build_upstream_url(
    target: str,
    path_params: list[tuple[str, str]],
    query_params: list[tuple[str, str]],
) -> str:
    """Substitute path params and append query params (Go: ``BuildRequest``)."""
    upstream = target
    for key, value in path_params:
        upstream = upstream.replace("{" + key + "}", quote(value, safe=""))
    if query_params:
        sep = "&" if "?" in upstream else "?"
        upstream += sep + urlencode(query_params)
    return upstream


def broker_request_url(scheme: str, host: str, upstream: str, *, broker_relative: bool) -> str:
    """Address the broker as the catch-all proxy (Go: ``BuildRequest``)."""
    if broker_relative:
        return f"{scheme}://{host}{upstream}"
    return f"{scheme}://{host}/{upstream}"


def _error_origin(headers: httpx.Headers) -> str:
    value: str = headers.get("Jentic-Error-Origin", "")
    return value.strip().lower()


def classify_denial(status: int, headers: httpx.Headers, body: bytes) -> ToolError | None:
    """Broker-denial classification (Go: ``agentops.Classify`` + ``executeDenialError``).

    A denial-class status (401/403/409/424) whose ``Jentic-Error-Origin`` is
    not ``upstream`` is the broker refusing the call; the verbatim
    ``agent_directive`` sub-object (when present) rides the soft error so the
    broker's recovery instructions reach the model intact.
    """
    if status not in _DENIAL_STATUSES or _error_origin(headers) == "upstream":
        return None
    directive: Any = None
    instruction = ""
    try:
        envelope = json.loads(body)
        directive = envelope.get("agent_directive") if isinstance(envelope, dict) else None
        if isinstance(directive, dict):
            instruction = str(directive.get("instruction") or "")
        else:
            directive = None
    except ValueError:
        directive = None
    extra: dict[str, Any] = {"retryable": False}
    if directive is not None:
        extra["agent_directive"] = directive
    return ToolError(
        CODE_BROKER_DENIED,
        "the broker denied this call before it reached the upstream API",
        actionable=instruction or _synthesized_denial_hint(status),
        details={"http_status": status},
        next_tool="whoami",
        extra=extra,
    )


def _synthesized_denial_hint(status: int) -> str:
    """Status-keyed recovery when the denial carried no directive (Go twin)."""
    if status == 403:
        return (
            "This agent isn't bound to a toolkit serving this API. Call whoami to see "
            "your bindings, then ask your operator to grant access "
            "(`jentic access request --toolkit <vendor/name> --wait`)."
        )
    if status == 424:
        return (
            "No credential is provisioned for this call. Ask your operator to provision "
            "one (`jentic access request --toolkit <vendor/name> --provision --wait`), "
            "then retry."
        )
    if status == 401:
        return (
            "The stored upstream credential needs reconnecting. Ask your operator to "
            "re-provision it (`jentic access request --toolkit <vendor/name> "
            "--provision --wait`), then retry."
        )
    return (
        "The broker denied this call before it reached the upstream API. "
        "Call whoami to check what you can run."
    )


def broker_redirect_error(status: int, headers: httpx.Headers) -> ToolError | None:
    """#1207 posture: a redirect the broker ITSELF answered is a coded error.

    One the broker merely mirrored from the upstream
    (``Jentic-Error-Origin: upstream``) is the caller's data and passes
    through.
    """
    if status not in _REDIRECT_STATUSES or _error_origin(headers) == "upstream":
        return None
    location = headers.get("Location", "")
    return ToolError(
        CODE_TRANSPORT_ERROR,
        f"broker answered an unexpected redirect (HTTP {status} to {location!r}); "
        "redirects are never followed on the broker leg",
        actionable="The broker must answer directly. Verify server.mcp.broker_url points "
        "at the actual broker (no redirecting proxy in front).",
    )


def transport_error(exc: Exception, *, retry_safe: bool) -> ToolError:
    """The coded transport-error row (Go: ``executeTransportError``).

    The ``retryable`` hint is earned, not blanket: ``True`` only when the
    caller proved the retry safe (GET/HEAD or an Idempotency-Key) or the
    failure is provably pre-send (connect/TLS — the upstream never saw the
    request). A mid-flight failure on an unkeyed mutating call may have
    already been delivered.
    """
    pre_send = isinstance(exc, httpx.ConnectError)
    retryable = retry_safe or pre_send
    message = f"transport error: {exc}"
    if retryable:
        return ToolError(
            CODE_TRANSPORT_ERROR,
            message,
            next_tool="get_started",
            extra={"retryable": True},
        )
    return ToolError(
        CODE_TRANSPORT_ERROR,
        message + " — the failure happened mid-flight, so the request may already have "
        "reached the upstream",
        actionable="Do not re-send this call blindly: it may have executed. Verify the "
        "effect with a read (execute_read) first, or re-send with an idempotency_key so "
        "the broker de-duplicates the retry. If the execute returned a held (202) job, "
        "poll it with get_execution_result — never re-send.",
        next_tool="get_started",
        extra={"retryable": False},
    )


def _cap_headers(headers: dict[str, str], budget: int) -> tuple[dict[str, str], bool]:
    """Bound the aggregate serialized header size (Go: ``capHeaders``)."""
    per_entry_overhead = 6
    total = sum(len(k) + len(v) + per_entry_overhead for k, v in headers.items())
    if total <= budget:
        return headers, False
    capped: dict[str, str] = {}
    used = 0
    for key in sorted(headers):
        cost = len(key) + len(headers[key]) + per_entry_overhead
        if used + cost > budget:
            continue
        used += cost
        capped[key] = headers[key]
    return capped, True


def execute_result_payload(
    status: int, headers: httpx.Headers, body: bytes, execution_id: str
) -> dict[str, Any]:
    """The tool payload for a relayed broker response (Go: ``executeResultPayload``).

    The exact CLI envelope keys with the size caps applied — including
    the held (202) envelope, which passes through with its directive intact
    (the model polls with ``get_execution_result``, never re-sends).
    """
    single_valued = {key: headers[key] for key in headers}
    # Header names arrive lowercase from httpx; the CLI envelope (and the
    # shared goldens) carry Go's canonical MIME casing — restore it so the
    # envelope bytes match across implementations.
    canonical = {_canonical_header(k): v for k, v in single_valued.items()}
    capped_headers, headers_truncated = _cap_headers(canonical, HEADER_BYTES_BUDGET)
    parsed_body: Any
    try:
        parsed_body = json.loads(body)
    except ValueError:
        parsed_body = body.decode("utf-8", errors="replace")
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "headers": capped_headers,
        "body": parsed_body,
    }
    if headers_truncated:
        payload["headers_truncated"] = True
    if execution_id:
        payload["execution_id"] = execution_id
    if len(body) > MAX_RESULT_BYTES:
        payload["body"] = body[:MAX_RESULT_BYTES].decode("utf-8", errors="ignore")
        payload["truncated"] = True
        payload["total_bytes"] = len(body)
    return payload


def _canonical_header(name: str) -> str:
    """Go's ``textproto.CanonicalMIMEHeaderKey`` casing for one header name."""
    return "-".join(part.capitalize() if part else part for part in name.split("-"))


def _server_user_agent() -> str:
    """``jentic-mcp/<version> (http)`` — the broker's own resolver derives
    ``Origin.MCP`` from the ``jentic-mcp/`` UA prefix (httpx's default UA
    would classify these executions ``Origin.API`` and the
    per-agent "last active" signal would miss HTTP-mount traffic)."""
    try:
        v = version("jentic-one")
    except PackageNotFoundError:  # pragma: no cover - dev checkouts always resolve
        v = "0.0.0"
    return f"jentic-mcp/{v} (http)"


def _broker_client() -> httpx.AsyncClient:
    """The broker-leg HTTP client (a seam so tests inject a mock transport)."""
    return httpx.AsyncClient(timeout=_EXECUTE_TIMEOUT_SECONDS, follow_redirects=False)


class BodyTooLargeError(Exception):
    """The broker/upstream body tripped ``_MAX_BODY_BYTES`` mid-stream.

    The Go twin's ``sdkclient.ReadAllBounded`` posture: fail closed instead of
    buffering without bound — the read stops at the cap, the connection is
    dropped, and the failure surfaces as the coded transport row (never a
    truncated success: by the time the cap trips, the envelope caps in
    :func:`execute_result_payload` could no longer report an honest
    ``total_bytes``).
    """

    def __init__(self, limit: int) -> None:
        super().__init__(
            f"read response: response body exceeds maximum allowed size (limit {limit} bytes)"
        )


async def _read_bounded(response: httpx.Response, limit: int) -> bytes:
    """Accumulate the streamed body, refusing to read past ``limit`` bytes."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > limit:
            raise BodyTooLargeError(limit)
        chunks.append(chunk)
    return b"".join(chunks)


async def send_to_broker(
    *,
    method: str,
    broker_url: str,
    credential: str,
    headers: list[tuple[str, str]],
    body: bytes | None,
    session_id: str | None,
    idempotency_key: str | None,
) -> tuple[int, httpx.Headers, bytes, str]:
    """Send one built broker request and read the bounded response.

    The caller's own credential rides as the bearer (the broker authenticates
    the AGENT, not this mount); correlation headers mirror the Go path —
    ``X-Jentic-Session-Id`` when the inbound request carried one, a fresh
    ``traceparent`` unless the caller supplied its own. The response is
    STREAMED and the read stops at ``_MAX_BODY_BYTES`` (Go:
    ``ReadAllBounded``) — the full body is never buffered first.
    """
    request_headers: dict[str, str] = {}
    for key, value in headers:
        request_headers[key.strip()] = value.strip()
    lower_keys = {k.lower() for k in request_headers}
    if body is not None and "content-type" not in lower_keys:
        request_headers["Content-Type"] = "application/json"
    if session_id and "x-jentic-session-id" not in lower_keys:
        request_headers["X-Jentic-Session-Id"] = session_id
    if "traceparent" not in lower_keys:
        request_headers["traceparent"] = f"00-{uuid_mod.uuid4().hex}-{uuid_mod.uuid4().hex[:16]}-01"
    if idempotency_key and "idempotency-key" not in lower_keys:
        request_headers["Idempotency-Key"] = idempotency_key
    # Protected headers merge LAST so a model-supplied ``headers`` tool-arg
    # can never override the caller's bearer or the ``jentic-mcp/`` UA the
    # broker derives ``Origin.MCP`` from (case-insensitive duplicates are
    # dropped first — two spellings must not ride the wire). NOTE: the Go twin
    # (``agentops/execute.go``) still merges the other way — follow-up filed.
    for protected in ("authorization", "user-agent"):
        for key in [k for k in request_headers if k.lower() == protected]:
            del request_headers[key]
    request_headers["Authorization"] = f"Bearer {credential}"
    request_headers["User-Agent"] = _server_user_agent()

    async with (
        _broker_client() as client,
        client.stream(method, broker_url, headers=request_headers, content=body) as response,
    ):
        raw = await _read_bounded(response, _MAX_BODY_BYTES)
        status = response.status_code
        response_headers = response.headers
        execution_id = response.headers.get("Jentic-Execution-Id", "")
    return status, response_headers, raw, execution_id
