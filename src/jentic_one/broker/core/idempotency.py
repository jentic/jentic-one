"""Pure idempotency primitives: the request fingerprint.

Pure domain helper (no FastAPI/httpx/DB) so it unit-tests in isolation — the
``IdempotencyStore`` *service* (over the shared-state backend) lives in
``broker/services/idempotency.py`` per the layering convention.

The fingerprint is the stable identity of a request under a given
``Idempotency-Key``: a repeat of the *same* fingerprint replays the original
response; a *different* fingerprint under the same key is a ``409`` conflict
(the client reused a key for a different request).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from jentic_one.broker.core.headers import (
    HOP_BY_HOP_HEADERS,
    SPOOFABLE_HEADERS,
    connection_nominated_headers,
)

_SEP = b"\0"

# Headers that vary per transport attempt or carry caller authentication/tracing
# rather than upstream request semantics. Everything else forwarded to the
# upstream (media types, content negotiation, preconditions, range requests,
# vendor version headers, credential/revision selection, and custom headers)
# participates in the request fingerprint.
_VOLATILE_REQUEST_HEADERS: frozenset[str] = (
    HOP_BY_HOP_HEADERS
    | SPOOFABLE_HEADERS
    | frozenset(
        {
            "authorization",
            "b3",
            "content-length",
            "cookie",
            "date",
            "idempotency-key",
            "jentic-toolkit-id",
            "mcp-session-id",
            "prefer",
            " traceparent".strip(),
            "tracestate",
            "user-agent",
            "x-amzn-trace-id",
            "x-b3-flags",
            "x-b3-parentspanid",
            "x-b3-sampled",
            "x-b3-spanid",
            "x-b3-traceid",
            "x-correlation-id",
            "x-jentic-api-key",
            "x-request-id",
        }
    )
)


def canonicalize_cookie(cookie: str | None) -> str:
    """Return a whitespace-normalized cookie header string preserving pair order."""
    if not cookie:
        return ""
    parts = [segment.strip() for segment in cookie.split(";") if segment.strip()]
    return "; ".join(parts)


def _normalize_content_type(value: str) -> str:
    """Normalize a Content-Type header value (lowercase media type and parameter names)."""
    segments = [seg.strip() for seg in value.split(";") if seg.strip()]
    if not segments:
        return ""
    media_type = segments[0].lower()
    params: list[str] = []
    for seg in segments[1:]:
        if "=" in seg:
            k, v = seg.split("=", 1)
            k_norm = k.strip().lower()
            v_norm = v.strip()
            if k_norm == "charset":
                v_norm = v_norm.strip('"').lower()
            params.append(f"{k_norm}={v_norm}")
        else:
            params.append(seg.lower())
    params.sort()
    return "; ".join([media_type, *params])


def canonicalize_semantic_headers(headers: Mapping[str, str] | None) -> list[tuple[str, str]]:
    """Extract and canonicalize semantic request headers in deterministic order."""
    if not headers:
        return []
    nominated = connection_nominated_headers(headers)
    canonical: list[tuple[str, str]] = []
    for raw_name, raw_value in headers.items():
        name = raw_name.strip().lower()
        if not name or name in _VOLATILE_REQUEST_HEADERS or name in nominated:
            continue
        value = raw_value.strip()
        if name == "accept-encoding":
            value = value.lower()
            if value == "identity":
                # The HTTP runner injects identity when this header is absent.
                # Omitting the explicit default keeps equivalent retries on the
                # same fingerprint without changing fingerprints that omit it.
                continue
        if name == "content-type":
            value = _normalize_content_type(value)
        canonical.append((name, value))
    canonical.sort()
    return canonical


def fingerprint(
    method: str,
    url: str,
    consumer_id: str,
    body: bytes | None,
    *,
    headers: Mapping[str, str] | None = None,
    cookie: str | None = None,
    server_variables: Mapping[str, str] | None = None,
) -> str:
    """Stable hash of the parts that define request identity and execution semantics.

    Includes the method, the reconstructed upstream URL, the consumer scope
    (the selected credential id), a hash of the request body, the inbound
    ``Cookie`` header, semantic forwarded headers (such as ``Content-Type``,
    ``Accept``, conditional headers, range headers, and vendor version headers),
    and resolved server variables.
    """
    h = hashlib.sha256()
    for part in (method.upper(), url, consumer_id):
        h.update(part.encode())
        h.update(_SEP)
    h.update(hashlib.sha256(body or b"").digest())

    nominated = connection_nominated_headers(headers) if headers else frozenset()
    effective_cookie = None if "cookie" in nominated else cookie
    if effective_cookie is None and headers and "cookie" not in nominated:
        cookie_values = [v for k, v in headers.items() if k.strip().lower() == "cookie" and v]
        if cookie_values:
            effective_cookie = "; ".join(cookie_values)

    norm_cookie = canonicalize_cookie(effective_cookie)
    if norm_cookie:
        h.update(_SEP)
        h.update(b"cookie")
        h.update(_SEP)
        h.update(norm_cookie.encode())

    norm_headers = canonicalize_semantic_headers(headers)
    if norm_headers:
        h.update(_SEP)
        h.update(b"headers")
        for name, value in norm_headers:
            h.update(_SEP)
            h.update(name.encode())
            h.update(_SEP)
            h.update(value.encode())

    if server_variables:
        h.update(_SEP)
        h.update(b"server_variables")
        for var_name, var_val in sorted(server_variables.items()):
            h.update(_SEP)
            h.update(var_name.encode())
            h.update(_SEP)
            h.update(var_val.encode())

    return h.hexdigest()


compute_request_fingerprint = fingerprint

__all__ = [
    "canonicalize_cookie",
    "canonicalize_semantic_headers",
    "compute_request_fingerprint",
    "fingerprint",
]
