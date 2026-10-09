"""Pure helpers for reconstructing the upstream URL and filtering proxied headers.

Both are pure functions (no FastAPI/httpx/DB) so they unit-test in isolation —
domain layer per the 00-overview layering table.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from jentic_one.broker.core.headers import (
    BROKER_CONSUMED_HEADERS,
    HOP_BY_HOP_HEADERS,
    SPOOFABLE_HEADERS,
)

# Restore the scheme's doubled slash that Starlette collapses (``https:/x`` →
# ``https://x``) — applied to the *raw* path only.
_SCHEME_RE = re.compile(r"^(https?):/([^/])")

# Response headers that describe the *encoded* upstream body. httpx decompresses
# the body before we see it, so these no longer match ``RunnerResult.body`` and
# must be dropped (the ASGI server recomputes ``content-length``).
_DECODED_BODY_HEADERS: frozenset[str] = frozenset({"content-length", "content-encoding"})

# Inbound framing that describes the *caller's* body, not the outbound one. The
# broker drops the body on body-less methods (GET, HEAD, OPTIONS) and httpx
# frames whatever body it does send, so a forwarded ``content-length`` can only
# be stale: the HTTP client rejects a declared length that the body does not
# match, failing the request before it reaches the upstream.
_INBOUND_FRAMING_HEADERS: frozenset[str] = frozenset({"content-length"})

# Request headers a queued or held execution keeps for its later run. An async
# job's payload sits in the admin DB (a held one for its whole approval
# window), so it stores only headers that describe the body and the response
# the caller wants — never the caller's own credentials or arbitrary headers.
# The run re-injects upstream credentials itself.
REPLAY_HEADERS: frozenset[str] = frozenset(
    {
        "accept",
        "accept-language",
        "content-encoding",
        "content-language",
        "content-type",
        "if-match",
        "if-modified-since",
        "if-none-match",
        "if-unmodified-since",
    }
)


def reconstruct_upstream_url(scope: Mapping[str, Any]) -> str:
    """Rebuild the upstream URL from the raw ASGI scope, byte-exact.

    Reconstructs from ``scope["raw_path"]`` / ``scope["query_string"]`` — **never**
    the decoded ``{upstream_url:path}`` param — so percent-encoding survives
    (``%2F`` stays ``%2F``). Forwarding the decoded param would mutilate a path
    segment that legitimately contains an encoded slash (a single segment
    ``jentic%2Fcore`` would split into two), causing upstream 404s or a
    path-traversal primitive.
    """
    raw_path_bytes = scope.get("raw_path") or scope.get("path", "").encode("latin-1")
    raw_path = raw_path_bytes.decode("latin-1")
    query_string = scope.get("query_string", b"").decode("latin-1")

    raw_path = raw_path.lstrip("/")
    url = _SCHEME_RE.sub(r"\1://\2", raw_path)
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    if query_string:
        url = f"{url}?{query_string}"
    return url


def forward_headers(inbound: Mapping[str, str], injected: Mapping[str, str]) -> dict[str, str]:
    """Filter inbound request headers for forwarding, then apply injected auth.

    Strips hop-by-hop (incl. ``Host``), broker-consumed, and spoofable
    forwarding/topology headers, plus the inbound ``Content-Length``: httpx
    computes the outbound length from the body actually sent. Injected auth
    headers win on conflict.

    ``Cookie`` is intentionally **not** special-cased here: a cookie-located
    credential is merged explicitly at the call site by appending to the
    forwarded ``Cookie`` rather than overwriting it.
    """
    out = {
        key: value
        for key, value in inbound.items()
        if key.lower() not in HOP_BY_HOP_HEADERS
        and key.lower() not in BROKER_CONSUMED_HEADERS
        and key.lower() not in SPOOFABLE_HEADERS
        and key.lower() not in _INBOUND_FRAMING_HEADERS
    }
    out.update(injected)
    return out


def replay_headers(inbound: Mapping[str, str]) -> dict[str, str]:
    """The inbound headers in :data:`REPLAY_HEADERS`, keyed lower-case.

    What an async or held execution's job payload keeps so its run sends the
    body as the caller described it (``Content-Type`` above all).
    """
    return {key.lower(): value for key, value in inbound.items() if key.lower() in REPLAY_HEADERS}


def passthrough_response_headers(upstream: Mapping[str, str]) -> dict[str, str]:
    """Filter upstream *response* headers for passthrough.

    Strips hop-by-hop plus ``content-length``/``content-encoding``: httpx
    transparently decompresses the response, so ``RunnerResult.body`` is the
    *decoded* bytes — forwarding the upstream ``content-encoding`` (e.g. ``gzip``)
    or its now-stale ``content-length`` would misdescribe the body to the
    downstream client. The ASGI server recomputes ``content-length`` from the
    actual body. Everything else passes through verbatim — no ``x-upstream-``
    prefixing (B-002).
    """
    return {
        key: value
        for key, value in upstream.items()
        if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() not in _DECODED_BODY_HEADERS
    }


def passthrough_streaming_headers(upstream: Mapping[str, str]) -> dict[str, str]:
    """Filter upstream response headers for the **raw streaming** passthrough.

    The streaming path forwards the upstream body byte-for-byte via ``aiter_raw``
    (still-compressed), so — unlike the buffered path — ``content-encoding`` is
    **preserved** (the bytes really are still in that encoding). ``content-length``
    is dropped: we may abort the stream early (size cap / deadline), and a partial
    body must not be described by the original full length; the ASGI server uses
    chunked transfer instead. Only hop-by-hop headers are otherwise stripped.
    """
    return {
        key: value
        for key, value in upstream.items()
        if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() != "content-length"
    }
