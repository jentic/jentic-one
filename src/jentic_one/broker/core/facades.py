"""Vendor CLI facades: path-mode addressing, header policy and vendor error bodies.

A facade is a broker endpoint that speaks one vendor's API shape, so the
vendor's own CLI (``cf``, ``aws``) can be pointed at it with a base-URL
override and authenticated with a ``cit_`` invocation token. Each call is
mapped to the vendor's real upstream URL and run through the same execute
pipeline as the forward proxy (registry match, rules, credential injection,
execution record).

Path-mode facades live under :data:`FACADE_PATH_PREFIX`. Its first segment
starts with an underscore, which no DNS hostname can contain, so the reserved
prefix never shadows a URL the forward-proxy catch-all could otherwise serve
(the catch-all reads the first path segment as the upstream host).

Pure module: no FastAPI, httpx or DB imports.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

#: The reserved path prefix every path-mode facade is served under.
FACADE_PATH_PREFIX = "/_facade"


@dataclass(frozen=True, slots=True)
class PathFacade:
    """A facade served under ``prefix`` that forwards to ``upstream_base``.

    ``/_facade/<vendor>/<rest>?<query>`` maps to ``<upstream_base>/<rest>?<query>``
    with ``rest`` and ``query`` kept exactly as the client encoded them.
    """

    vendor: str
    prefix: str
    upstream_base: str


#: Cloudflare (``cf``): ``CLOUDFLARE_API_BASE_URL=<broker>/_facade/cloudflare``.
CLOUDFLARE_FACADE = PathFacade(
    vendor="cloudflare",
    prefix=f"{FACADE_PATH_PREFIX}/cloudflare",
    upstream_base="https://api.cloudflare.com/client/v4",
)


def facade_upstream_url(scope: Mapping[str, Any], facade: PathFacade) -> str | None:
    """The upstream URL for a facade request, built from the raw ASGI path.

    Like the forward proxy, this reads ``scope["raw_path"]`` and
    ``scope["query_string"]``, never the decoded path parameter, so an
    encoded ``%2F`` inside a segment reaches the upstream as ``%2F``.

    Returns ``None`` (the caller answers 404) when the raw path does not start
    with the literal prefix (e.g. the prefix itself was percent-encoded, so the
    route matched only after decoding) or when the remainder has a ``.`` or
    ``..`` segment, literal or encoded, which could step outside the vendor's
    API base path.
    """
    raw_path_bytes = scope.get("raw_path") or scope.get("path", "").encode("latin-1")
    raw_path = raw_path_bytes.decode("latin-1")
    lead = f"{facade.prefix}/"
    if not raw_path.startswith(lead):
        return None
    remainder = raw_path[len(lead) :]
    if any(unquote(segment) in (".", "..") for segment in remainder.split("/")):
        return None
    url = f"{facade.upstream_base}/{remainder}"
    query_string = scope.get("query_string", b"").decode("latin-1")
    if query_string:
        url = f"{url}?{query_string}"
    return url


# A ``cit_`` invocation token as minted (``cit_`` + 43 URL-safe base64 chars),
# not preceded by a word character so ``explicit_…`` never matches.
_INVOCATION_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])cit_[A-Za-z0-9_-]{32,}")

# Inbound trace context: a facade call runs on the trace its token was minted
# with, so a client-supplied context must not reach the upstream either.
_INBOUND_TRACE_HEADERS: frozenset[str] = frozenset({"traceparent", "tracestate", "baggage"})


def facade_request_headers(inbound: Mapping[str, str]) -> dict[str, str]:
    """Inbound headers a facade may forward, before the proxy's own filtering.

    Drops every header whose value carries an invocation token (``Authorization``
    is already a broker-consumed header; this also covers a CLI or plugin that
    repeats the token elsewhere) and the inbound trace context headers. The
    result still goes through ``forward_headers``.
    """
    return {
        key: value
        for key, value in inbound.items()
        if key.lower() not in _INBOUND_TRACE_HEADERS and not _INVOCATION_TOKEN_RE.search(value)
    }


#: Upstream response headers a facade passes on to the CLI. The facade answers
#: on the broker's own origin, so anything that could set state there
#: (``Set-Cookie``), redirect the CLI straight to the vendor (``Location``) or
#: advertise vendor auth (``WWW-Authenticate``) is dropped.
FACADE_RESPONSE_HEADERS: frozenset[str] = frozenset(
    {
        "cache-control",
        "content-disposition",
        "content-encoding",
        "content-language",
        "content-length",
        "content-type",
        "date",
        "etag",
        "expires",
        "last-modified",
        "retry-after",
        "vary",
        "ratelimit",
        "ratelimit-policy",
        "x-ratelimit-limit",
        "x-ratelimit-remaining",
        "x-ratelimit-reset",
        "cf-ray",
        "idempotent-replayed",
        "preference-applied",
        "tracestate",
    }
)


def is_facade_response_header(name: str) -> bool:
    """Whether a facade passes response header ``name`` on (the allowlist or ``Jentic-*``)."""
    key = name.lower()
    return key in FACADE_RESPONSE_HEADERS or key.startswith("jentic-")


#: The instruction a facade's held-call error carries: the CLI has already
#: failed, and the broker runs the call itself once a reviewer approves it.
FACADE_HELD_INSTRUCTION = (
    "Show the user the review link: they cannot approve the call without it. Do not "
    "re-run the command. Once approved, the call runs exactly once on its own and its "
    "outcome is at the result link; steps the command would have taken after this call "
    "did not happen."
)


def facade_error_message(problem: Mapping[str, Any]) -> str:
    """One line for a CLI: the problem's ``detail`` plus its agent directive, if any."""
    detail = problem.get("detail")
    if not isinstance(detail, str) or not detail:
        title = problem.get("title")
        detail = title if isinstance(title, str) and title else "The request failed."
    message = f"Jentic: {detail}"
    directive = problem.get("agent_directive")
    instruction = (
        directive.get("human_readable_instruction") if isinstance(directive, Mapping) else None
    )
    if isinstance(instruction, str) and instruction:
        message = f"{message} {instruction}"
    return message


def cloudflare_error_body(status: int, problem: Mapping[str, Any]) -> dict[str, Any]:
    """A Cloudflare API v4 error envelope for a broker problem.

    ``code`` is the HTTP status: Cloudflare's own error codes are not reused,
    so a Jentic refusal is never mistaken for one from Cloudflare.
    """
    return {
        "success": False,
        "errors": [{"code": status, "message": facade_error_message(problem)}],
        "messages": [],
        "result": None,
    }


__all__ = [
    "CLOUDFLARE_FACADE",
    "FACADE_HELD_INSTRUCTION",
    "FACADE_PATH_PREFIX",
    "FACADE_RESPONSE_HEADERS",
    "PathFacade",
    "cloudflare_error_body",
    "facade_error_message",
    "facade_request_headers",
    "facade_upstream_url",
    "is_facade_response_header",
]
