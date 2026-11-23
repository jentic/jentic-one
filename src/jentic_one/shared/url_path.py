"""Canonical URL-path normalization and template toolkit shared by discovery and enforcement.

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
spelled with escapes or backslashes is read differently by different upstream
stacks; :func:`has_ambiguous_traversal` lets enforcement refuse it outright.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import unquote

PERCENT_ENCODED_RE = re.compile(r"%[0-9A-Fa-f]{2}")
UNRESERVED_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
# Escapes that stay encoded (uppercased): decoding them changes the path's
# segment structure or enables a second decode.
_KEPT_ENCODED = frozenset({"%2F", "%5C", "%25"})
_SEPARATOR_RE = re.compile(r"/|\\|%2[Ff]|%5[Cc]")

PATH_PARAM_RE = re.compile(r"\{([^}]+)\}")
# RFC 6570 expression operators. These are single-character prefixes on the
# expression (e.g. ``{+var}``, ``{#var}``), never part of the variable name, so
# they must be stripped before a templated token is reconciled against a declared
# ``in: path`` parameter name. ``+`` (reserved expansion) additionally signals a
# catch-all that matches across path separators.
RFC6570_OPERATORS = frozenset("+#./;?&")
_SAFE_GROUP_RE = re.compile(r"[^a-zA-Z0-9_]")


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
    """True if ``path`` has a dot segment spelled with escapes or backslashes.

    ``/a%2F..%2Fb`` and ``/a/%2e%2e/b`` are a traversal to a stack that decodes
    the escape before resolving dot segments and a literal segment to one that
    does not. A literal ``\\`` is the same split: ``/a/..\\b`` is a traversal
    to a stack that treats ``\\`` as a separator and one segment ``..\\b`` to
    one that does not. No single path can be rule-checked for both readings,
    so enforcement denies such a request instead of guessing.
    """
    if "%" not in path and "\\" not in path:
        return False
    has_ambiguous_separator = re.search(r"%2[Ff]|%5[Cc]|\\", path) is not None
    for segment in _SEPARATOR_RE.split(path):
        decoded = unquote(segment)
        if decoded in (".", "..") and (has_ambiguous_separator or segment != decoded):
            return True
    return False


def split_param_token(token: str) -> tuple[str, bool]:
    """Split a path-param token into ``(name, is_catch_all)``.

    OpenAPI / RFC 6570 expressions may carry a single-character operator prefix
    (``+#./;?&``) that is *not* part of the variable name — Google discovery-derived
    specs template reserved-expansion params as ``{+property}`` while declaring the
    parameter plainly as ``property``. Any such operator is stripped from the
    returned name so the token reconciles with its declared ``in: path`` parameter.

    The reserved-expansion operator (``+``) additionally marks a catch-all that
    matches across path separators (``.+``); a plain ``{param}`` matches a single
    segment (``[^/]+``).
    """
    is_catch_all = token.startswith("+")
    if token and token[0] in RFC6570_OPERATORS:
        return token[1:], is_catch_all
    return token, is_catch_all


def normalize_path_template(template: str) -> str:
    """Normalize a path template to the same canonical form as ``normalize_path``.

    ``{...}`` parameter tokens are preserved verbatim (including RFC 6570
    operators such as ``{+param}``); everything else — trailing slash, dot
    segments, percent-encoding — is normalized exactly as ``normalize_path``
    normalizes a request path. This symmetry is what lets an index entry built
    from the template match a request path normalized at lookup time (#1085).

    Tokens are shielded behind NUL-delimited sentinels while the whole template
    goes through ``normalize_path`` in one pass; normalizing literal chunks
    individually would strip the slash *before* a token (``/pets/{petId}`` →
    ``/pets{petId}``), because each chunk's trailing slash looks like a
    trailing slash to ``normalize_path``.
    """
    parts = PATH_PARAM_RE.split(template)
    tokens: list[str] = []
    shielded: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            shielded.append(part)
        else:
            tokens.append(part)
            shielded.append(f"\x00{len(tokens) - 1}\x00")
    normalized = normalize_path("".join(shielded))
    for idx, token in enumerate(tokens):
        normalized = normalized.replace(f"\x00{idx}\x00", "{" + token + "}")
    return normalized


def regex_group_base(name: str) -> str:
    """Sanitize a path-parameter *name* into a valid Python regex group identifier."""
    base = _SAFE_GROUP_RE.sub("_", name)
    if not base or base[0].isdigit():
        return f"p_{base}"
    return base


def allocate_group_names(tokens: Iterable[str]) -> dict[str, str]:
    """Map each path-parameter name in ``tokens`` to its unique regex group name.

    A name is allocated the first time it appears, scanning ``tokens`` in
    order. Its group is :func:`regex_group_base` of the name unless a different
    name already holds that group (e.g. ``{a.b}`` and ``{a_b}``, or ``{1a}`` and
    ``{p_1a}``); the later name then takes the first free ``<group>__<n>``
    (``n`` from 2), so two distinct parameters never share a capture.
    """
    groups: dict[str, str] = {}
    taken: set[str] = set()
    for token in tokens:
        name, _is_catch_all = split_param_token(token)
        if name in groups:
            continue
        base = regex_group_base(name)
        group = base
        suffix = 2
        while group in taken:
            group = f"{base}__{suffix}"
            suffix += 1
        groups[name] = group
        taken.add(group)
    return groups
