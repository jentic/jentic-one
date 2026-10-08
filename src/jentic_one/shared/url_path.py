"""Canonical URL-path normalization shared by discovery and rule enforcement.

The registry's URL lookup matches a request against the index on the
normalized path, and the broker evaluates binding permission rules on the
same normalized form. Keeping one implementation here (importable from both
the registry and the broker) means a request can never resolve to an
operation under one spelling of its path and be rule-checked under another
(``/%61dmin`` resolves to ``/admin`` — a ``deny /admin`` rule must see
``/admin`` too).
"""

from __future__ import annotations

import re
from urllib.parse import unquote

PERCENT_ENCODED_RE = re.compile(r"%[0-9A-Fa-f]{2}")
UNRESERVED_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def _normalize_percent_encoding(path: str) -> str:
    """Normalize percent-encoding: decode unreserved chars, uppercase remaining."""

    def _replace(match: re.Match[str]) -> str:
        encoded = match.group(0)
        char = chr(int(encoded[1:], 16))
        if char in UNRESERVED_CHARS:
            return char
        return encoded.upper()

    return PERCENT_ENCODED_RE.sub(_replace, path)


def _resolve_dot_segments(path: str) -> str:
    """Resolve . and .. segments in a path per RFC 3986."""
    segments = path.split("/")
    output: list[str] = []
    for segment in segments:
        if segment == ".":
            continue
        elif segment == "..":
            if output:
                output.pop()
        else:
            output.append(segment)
    resolved = "/".join(output)
    if path.startswith("/") and not resolved.startswith("/"):
        resolved = "/" + resolved
    return resolved


def normalize_path(path: str) -> str:
    """Normalize a URL path: decode, resolve dots, normalize encoding."""
    decoded = unquote(path)
    resolved = _resolve_dot_segments(decoded)
    normalized = _normalize_percent_encoding(resolved)
    if normalized and not normalized.startswith("/"):
        normalized = "/" + normalized
    return normalized.rstrip("/") or "/"
