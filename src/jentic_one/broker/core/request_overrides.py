"""Detect request method and request-target override headers and query parameters.

Pure module — no FastAPI/httpx/DB imports.
"""

from __future__ import annotations

from urllib.parse import unquote, unquote_plus, urlsplit

from starlette.datastructures import Headers

from jentic_one.broker.core.headers import extract_header_items

METHOD_OVERRIDE_HEADERS: frozenset[str] = frozenset(
    {
        "x-http-method-override",
        "x-http-method",
        "x-method-override",
    }
)
TARGET_OVERRIDE_HEADERS: frozenset[str] = frozenset(
    {
        "x-original-url",
        "x-rewrite-url",
        "x-original-uri",
    }
)
METHOD_OVERRIDE_QUERY_PARAMS: frozenset[str] = frozenset({"_method"})


def _iter_header_items(headers: Headers) -> list[tuple[str, str]]:
    raw = getattr(headers, "raw", None)
    if isinstance(raw, list):
        return [(k.decode("latin-1"), v.decode("latin-1")) for k, v in raw]
    return extract_header_items(headers)


def find_request_override(method: str, headers: Headers, url: str) -> str | None:
    """Return the offending header or query parameter name when a request override is present.

    Rejects:
    - any target-override header (``X-Original-URL``, ``X-Rewrite-URL``,
      ``X-Original-URI``), present at all;
    - a method-override header or ``_method`` query parameter (case-insensitive,
      across every header line) whose upper-cased value differs from ``method``.
    """
    expected_method = method.strip().upper()
    items = _iter_header_items(headers)
    for raw_name, _ in items:
        lowered = raw_name.strip().lower()
        if lowered in TARGET_OVERRIDE_HEADERS:
            return lowered
    for raw_name, raw_value in items:
        lowered = raw_name.strip().lower()
        if lowered in METHOD_OVERRIDE_HEADERS and raw_value.strip().upper() != expected_method:
            return lowered
    query = urlsplit(url).query
    if query:
        for segment in query.split("&"):
            if not segment:
                continue
            raw_key, _, raw_val = segment.partition("=")
            for candidate_key in (raw_key, unquote(raw_key), unquote_plus(raw_key)):
                lowered_key = candidate_key.lower()
                if lowered_key in METHOD_OVERRIDE_QUERY_PARAMS:
                    decoded_val = unquote_plus(raw_val).strip().upper()
                    if decoded_val != expected_method:
                        return lowered_key
                    break
    return None


__all__ = [
    "METHOD_OVERRIDE_HEADERS",
    "METHOD_OVERRIDE_QUERY_PARAMS",
    "TARGET_OVERRIDE_HEADERS",
    "find_request_override",
]
