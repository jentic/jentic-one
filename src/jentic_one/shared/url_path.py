"""Canonical URL-path normalization shared by discovery and rule enforcement.

The registry's URL lookup matches a request against the index on the
normalized path, and the broker evaluates binding permission rules on the
same normalized form. Keeping one implementation here (importable from both
the registry and the broker) means a request can never resolve to an
operation under one spelling of its path and be rule-checked under another
(``/%61dmin`` resolves to ``/admin`` — a ``deny /admin`` rule must see
``/admin`` too).

Decoding is **single-pass** and never decodes ``%2F`` / ``%5C`` (path
separators — ``\\`` is a separator to some upstream stacks) or ``%25`` (so
``%2561`` stays ``%2561`` instead of double-decoding to ``a``). The broker
forwards the raw URL, where ``%2F`` is data inside one segment; decoding it
here would let ``/admin%2F..%2Fwidgets`` be rule-checked as ``/widgets`` while
the upstream receives a path under ``/admin``. A path whose dot segments are
spelled with escapes is read differently by different upstream stacks;
:func:`has_ambiguous_traversal` lets enforcement refuse it outright.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

PERCENT_ENCODED_RE = re.compile(r"%[0-9A-Fa-f]{2}")
UNRESERVED_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
# Escapes that stay encoded (uppercased): decoding them changes the path's
# segment structure or enables a second decode.
_KEPT_ENCODED = frozenset({"%2F", "%5C", "%25"})
_SEPARATOR_RE = re.compile(r"/|\\|%2[Ff]|%5[Cc]")


def _decode_once(path: str) -> str:
    """Single-pass percent-decode, keeping separators and ``%`` encoded."""

    def _replace(match: re.Match[str]) -> str:
        encoded = match.group(0).upper()
        if encoded in _KEPT_ENCODED:
            return encoded
        return chr(int(encoded[1:], 16))

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
    """Normalize a URL path: decode once, resolve dot segments, trim the trailing slash.

    ``%2F``, ``%5C`` and ``%25`` stay encoded (uppercased) — see the module
    docstring.
    """
    resolved = _resolve_dot_segments(_decode_once(path))
    if resolved and not resolved.startswith("/"):
        resolved = "/" + resolved
    return resolved.rstrip("/") or "/"


def has_ambiguous_traversal(path: str) -> bool:
    """True if ``path`` has a dot segment spelled with escapes.

    ``/a%2F..%2Fb`` and ``/a/%2e%2e/b`` are a traversal to a stack that decodes
    the escape before resolving dot segments and a literal segment to one that
    does not — no single path can be rule-checked for both readings, so
    enforcement denies such a request instead of guessing.
    """
    if "%" not in path:
        return False
    has_encoded_separator = re.search(r"%2[Ff]|%5[Cc]", path) is not None
    for segment in _SEPARATOR_RE.split(path):
        decoded = unquote(segment)
        if decoded in (".", "..") and (has_encoded_separator or segment != decoded):
            return True
    return False
