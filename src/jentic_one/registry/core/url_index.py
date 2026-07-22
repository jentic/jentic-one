"""URL-index helper library — server URL parsing, path normalization, and index construction."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urlparse

SCHEME_DEFAULT_PORTS: dict[str, int] = {
    "http": 80,
    "https": 443,
    "ftp": 21,
}

PATH_PARAM_RE = re.compile(r"\{([^}]+)\}")
PERCENT_ENCODED_RE = re.compile(r"%[0-9A-Fa-f]{2}")
UNRESERVED_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")

# RFC 6570 expression operators. These are single-character prefixes on the
# expression (e.g. ``{+var}``, ``{#var}``), never part of the variable name, so
# they must be stripped before a templated token is reconciled against a declared
# ``in: path`` parameter name. ``+`` (reserved expansion) additionally signals a
# catch-all that matches across path separators.
RFC6570_OPERATORS = frozenset("+#./;?&")


@dataclass
class ParsedServerURL:
    """Parsed components of an OpenAPI server URL."""

    scheme: str
    host: str
    port: int | None
    path: str
    original: str


@dataclass
class URLIndexEntry:
    """An entry in the URL index for matching requests to operations."""

    host_pattern: str
    host_regex: re.Pattern[str]
    path_pattern: str
    path_regex: re.Pattern[str]
    segment_count: int
    param_names: list[str] = field(default_factory=list)


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


def normalize_path_template(template: str) -> str:
    """Normalize a path template, preserving parameter placeholders."""
    parts = PATH_PARAM_RE.split(template)
    normalized_parts: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            normalized_parts.append(normalize_path(part) if part else "")
        else:
            normalized_parts.append("{" + part + "}")
    return "".join(normalized_parts)


def normalise_host(host: str, scheme: str = "https") -> str:
    """Normalize a hostname: lowercase, strip default port."""
    host = host.lower()
    if ":" in host:
        hostname, port_str = host.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            return host
        default_port = SCHEME_DEFAULT_PORTS.get(scheme)
        if default_port and port == default_port:
            return hostname
        return host
    return host


def host_contains_variables(host: str) -> bool:
    """Check if a host string contains OpenAPI variable placeholders."""
    return bool(PATH_PARAM_RE.search(host))


def build_host_regex(host: str) -> re.Pattern[str]:
    """Build a regex pattern for matching a host with optional variables."""
    parts = PATH_PARAM_RE.split(host)
    regex_parts: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            regex_parts.append(re.escape(part))
        else:
            regex_parts.append(r"[^:/]+")
    return re.compile("^" + "".join(regex_parts) + "$", re.IGNORECASE)


def expand_server_variables(url_template: str, variables: list[Any]) -> str:
    """Expand an OpenAPI server URL template using default variable values.

    Variables without a default value are left as-is (the ``{name}`` placeholder
    is preserved) so downstream regex building can still match them.
    """
    result = url_template
    for var in variables:
        if var.default_value is None:
            continue
        placeholder = "{" + var.name + "}"
        result = result.replace(placeholder, var.default_value)
    return result


def parse_server_url(url: str) -> ParsedServerURL:
    """Parse a server URL into its components."""
    parsed = urlparse(url)
    scheme = parsed.scheme or "https"
    host = parsed.hostname or ""
    port: int | None = parsed.port
    path = parsed.path or "/"

    if port and SCHEME_DEFAULT_PORTS.get(scheme) == port:
        port = None

    return ParsedServerURL(
        scheme=scheme,
        host=normalise_host(f"{host}:{port}" if port else host, scheme),
        port=port,
        path=normalize_path(path),
        original=url,
    )


def is_relative_server_url(url: str) -> bool:
    """Check if a server URL is relative (no scheme)."""
    return not url.startswith("http://") and not url.startswith("https://")


def resolve_server_url(server_url: str, base_url: str = "") -> str:
    """Resolve a potentially relative server URL against a base."""
    if not is_relative_server_url(server_url):
        return server_url
    if base_url:
        return base_url.rstrip("/") + "/" + server_url.lstrip("/")
    return server_url


def iter_openapi_server_lists(
    spec: dict[str, Any],
    path: str,
    method: str,
) -> list[dict[str, Any]]:
    """Iterate through server lists at operation, path, and spec level."""
    paths: dict[str, Any] = spec.get("paths", {})
    path_item: dict[str, Any] = paths.get(path, {})
    operation: dict[str, Any] = path_item.get(method.lower(), {})

    operation_servers: list[dict[str, Any]] = operation.get("servers", [])
    if operation_servers:
        return operation_servers

    path_servers: list[dict[str, Any]] = path_item.get("servers", [])
    if path_servers:
        return path_servers

    result: list[dict[str, Any]] = spec.get("servers", [])
    return result


def merge_paths(base_path: str, operation_path: str) -> str:
    """Join a base with an operation path, collapsing the boundary slash.

    ``base_path`` is either a server *path* (when building the URL index from a
    parsed server, e.g. ``/v2``) or a full server *URL* (when callers reconstruct
    a fully-qualified operation URL, e.g. ``https://host/v2/``); the logic is
    purely string-level (strip a trailing slash off the base, ensure exactly one
    leading slash on the op path) so both are safe. Keeping a single helper means
    the URL the registry surfaces in ``search``/``inspect`` matches the path the
    broker indexed, instead of diverging into a ``host//path`` double slash.
    """
    base = base_path.rstrip("/")
    op = operation_path if operation_path.startswith("/") else "/" + operation_path
    return base + op


def _safe_param_name(name: str) -> str:
    """Make a parameter name safe for use in regex named groups."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def _split_param_token(token: str) -> tuple[str, bool]:
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


def reconcile_declared_path_params(path_template: str, declared_names: list[str]) -> list[str]:
    """Return declared path-parameter names that map to a token in the template.

    Reconciles OpenAPI ``in: path`` parameter *names* against the ``{...}`` tokens
    in a path template, stripping RFC 6570 operators from the tokens first. This is
    what keeps a declared ``property`` parameter from being silently dropped when
    the path templates it as ``{+property}`` (RFC 6570 reserved expansion) — the
    class of bug that made the GA4 Data API and other Google APIs uncallable (#759).

    Order follows ``declared_names``; a declared name with no matching token is
    omitted.
    """
    token_names = set(extract_param_names(path_template))
    return [name for name in declared_names if name in token_names]


def build_path_regex(path_template: str) -> re.Pattern[str]:
    """Build a regex for matching a path template with parameters.

    ``{param}`` matches a single path segment; ``{+param}`` is a catch-all that
    matches across segments.
    """
    parts = PATH_PARAM_RE.split(path_template)
    regex_parts: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            regex_parts.append(re.escape(part))
        else:
            name, is_catch_all = _split_param_token(part)
            safe_name = _safe_param_name(name)
            matcher = ".+" if is_catch_all else "[^/]+"
            regex_parts.append(f"(?P<{safe_name}>{matcher})")
    return re.compile("^" + "".join(regex_parts) + "$")


def structural_regex(path_template: str) -> str:
    """Build a structural regex that ignores parameter names.

    Two paths with the same structure but different param names produce the same
    structural regex. Catch-all (``{+param}``) and single-segment (``{param}``)
    params remain structurally distinct.
    """
    parts = PATH_PARAM_RE.split(path_template)
    regex_parts: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            regex_parts.append(re.escape(part))
        else:
            _name, is_catch_all = _split_param_token(part)
            regex_parts.append(".+" if is_catch_all else "[^/]+")
    return "^" + "".join(regex_parts) + "$"


def extract_param_names(path_template: str) -> list[str]:
    """Extract parameter names from a path template (``+`` prefix stripped)."""
    return [_split_param_token(token)[0] for token in PATH_PARAM_RE.findall(path_template)]


def count_segments(path: str) -> int:
    """Count path segments. Returns -1 for catch-all paths."""
    if "**" in path or "{+" in path:
        return -1
    stripped = path.strip("/")
    if not stripped:
        return 0
    return len(stripped.split("/"))


def build_index_entry(
    host: str,
    path_template: str,
    scheme: str = "https",
) -> URLIndexEntry:
    """Build a complete URL index entry for an operation."""
    normalized_host = normalise_host(host, scheme)
    host_regex = build_host_regex(normalized_host)
    path_regex = build_path_regex(path_template)
    param_names = extract_param_names(path_template)
    segment_count = count_segments(path_template)

    return URLIndexEntry(
        host_pattern=normalized_host,
        host_regex=host_regex,
        path_pattern=path_template,
        path_regex=path_regex,
        segment_count=segment_count,
        param_names=param_names,
    )
