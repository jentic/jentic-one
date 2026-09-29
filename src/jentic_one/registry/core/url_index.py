"""URL-index helper library — server URL parsing, path normalization, and index construction."""

from __future__ import annotations

import itertools
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
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


URL_INDEX_FORMAT_MARKER = "(?#sv)"
"""Regex comment prefixed to every ``path_regex`` this module builds.

Rows carrying it were built with server-variable capture groups (see
:func:`build_server_index_entries`), so a match yields the request's concrete
server-variable values directly. A row *without* it predates that and was built
from the defaults-expanded server URL; ``URLLookupService`` re-derives the
values of such a row from the operation's stored servers instead. The comment
is zero-width, so it never changes what a regex matches.
"""


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
    """Build a complete URL index entry for an operation.

    The template is canonicalized through ``normalize_path_template`` first and
    every derived field (regex, params, segment count, stored pattern) comes
    from that canonical form. ``URLLookupService.resolve`` normalizes the
    incoming request path with ``normalize_path`` before matching, so both
    sides of the comparison must agree on one canonical form — building the
    regex from the raw template made any trailing-slash path unmatchable
    (#1085).
    """
    normalized_host = normalise_host(host, scheme)
    host_regex = build_host_regex(normalized_host)
    normalized_template = normalize_path_template(path_template)
    path_regex = build_path_regex(normalized_template)
    param_names = extract_param_names(normalized_template)
    segment_count = count_segments(normalized_template)

    return URLIndexEntry(
        host_pattern=normalized_host,
        host_regex=host_regex,
        path_pattern=normalized_template,
        path_regex=re.compile(URL_INDEX_FORMAT_MARKER + path_regex.pattern),
        segment_count=segment_count,
        param_names=param_names,
    )


# ---------------------------------------------------------------------------
# Server-variable expansion
# ---------------------------------------------------------------------------

MAX_SERVER_VARIABLE_EXPANSIONS = 32
"""Upper bound on URL-index entries generated per (server URL, operation) pair.

Every declared enum value (and the default of a free-form variable) gets its
own concrete index entry, so the per-server entry count is the cross-product
of each variable's options. When that product exceeds this cap the builder
falls back to two entries — the all-defaults URL and the templated URL (free
-form path variables still match as a pattern) — so a spec with many large
enums cannot blow up the index. The fallback is logged by the ingest stage.
"""

# Regex group prefix for a server-variable capture in ``host_regex`` /
# ``path_regex``. The group name carries the variable name (and its default,
# when declared) hex-encoded, so a matched index row alone is enough to recover
# the concrete variable values of the request URL — no extra lookup.
_SV_GROUP_PREFIX = "sv__"
# Prefix of an *empty* regex group that pins a server variable to a value the
# row's URL bakes in without a capturable position (an empty value, or the
# default-expanded fallback for a variable in the port). The group name carries
# the variable name and value hex-encoded; it always captures ``""``.
_SV_FIXED_GROUP_PREFIX = "svk__"
# Internal placeholder token for a declared server variable while the server
# URL goes through parsing/normalization (``{~sv0}``). ``~`` never starts an
# OpenAPI variable or path-parameter name in practice, and the braces keep the
# token shielded by ``normalize_path_template``.
_SV_TOKEN_PREFIX = "~sv"
# A free-form (enum-less) server variable in the *path* matches one segment.
# Free-form host variables are never indexed as a pattern: the host a
# credential is sent to must be one the spec names (an enum value or the
# default) or one a credential supplies for a templated ``{name}`` request —
# never a label the caller picks.
_FREE_PATH_VALUE = r"[^/]+"


@dataclass(frozen=True)
class ServerVariableSpec:
    """A declared OpenAPI server variable (``servers[].variables.<name>``)."""

    name: str
    default: str | None = None
    enum: tuple[str, ...] = ()


class _BindingKind(StrEnum):
    VALUE = "value"  # a concrete declared value (enum member or default)
    FREE = "free"  # any value — free-form path variable matched as a pattern
    TEMPLATE = "template"  # the literal ``{name}`` placeholder, left for substitution


@dataclass(frozen=True)
class _Binding:
    kind: _BindingKind
    value: str | None = None


@dataclass
class ServerIndexExpansion:
    """URL-index entries for one server URL + operation path."""

    entries: list[URLIndexEntry]
    capped: bool = False


@dataclass
class ServerVariableMatch:
    """Server-variable groups split out of a matched index row.

    ``values`` are the concrete values present in the request URL;
    ``defaults`` are the declared defaults of variables the request left as a
    literal ``{name}`` placeholder (for substitution when no credential
    supplies a value).
    """

    path_params: dict[str, str]
    values: dict[str, str]
    defaults: dict[str, str]


def _scalar_str(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (str, int, float)):
        return str(value)
    return None


def server_variable_specs(variables: Any) -> list[ServerVariableSpec]:
    """Normalize declared server variables into :class:`ServerVariableSpec`.

    Accepts the OpenAPI mapping form (``{name: {default, enum}}``) and a list
    of objects/dicts carrying ``name`` + ``default_value``/``default`` + ``enum``
    (the ORM ``ServerVariable`` shape). Non-scalar enum members are skipped.
    """
    items: list[tuple[str, Any, Any]] = []
    if isinstance(variables, Mapping):
        for name, spec in variables.items():
            spec_map = spec if isinstance(spec, Mapping) else {}
            items.append((str(name), spec_map.get("default"), spec_map.get("enum")))
    elif isinstance(variables, Sequence) and not isinstance(variables, str):
        for var in variables:
            if isinstance(var, Mapping):
                name = var.get("name")
                default = var.get("default_value", var.get("default"))
                enum = var.get("enum")
            else:
                name = getattr(var, "name", None)
                default = getattr(var, "default_value", getattr(var, "default", None))
                enum = getattr(var, "enum", None)
            if name:
                items.append((str(name), default, enum))

    specs: list[ServerVariableSpec] = []
    seen: set[str] = set()
    for name, default, enum in items:
        if name in seen:
            continue
        seen.add(name)
        members: list[str] = []
        if isinstance(enum, Sequence) and not isinstance(enum, str):
            for member in enum:
                text = _scalar_str(member)
                if text is not None and text not in members:
                    members.append(text)
        specs.append(
            ServerVariableSpec(name=name, default=_scalar_str(default), enum=tuple(members))
        )
    return specs


def _sv_group_name(spec: ServerVariableSpec) -> str:
    name_hex = spec.name.encode().hex()
    if spec.default is None:
        return f"{_SV_GROUP_PREFIX}{name_hex}"
    return f"{_SV_GROUP_PREFIX}{name_hex}_{spec.default.encode().hex()}"


def _decode_sv_group_name(group: str) -> tuple[str, str | None] | None:
    if not group.startswith(_SV_GROUP_PREFIX):
        return None
    encoded = group[len(_SV_GROUP_PREFIX) :]
    name_hex, _, default_hex = encoded.partition("_")
    try:
        name = bytes.fromhex(name_hex).decode()
        default = bytes.fromhex(default_hex).decode() if default_hex else None
    except ValueError:
        return None
    return name, default


def _fixed_value_group(name: str, value: str) -> str:
    return f"(?P<{_SV_FIXED_GROUP_PREFIX}{name.encode().hex()}_{value.encode().hex()}>)"


def _decode_fixed_group_name(group: str) -> tuple[str, str] | None:
    if not group.startswith(_SV_FIXED_GROUP_PREFIX):
        return None
    name_hex, _, value_hex = group[len(_SV_FIXED_GROUP_PREFIX) :].partition("_")
    try:
        return bytes.fromhex(name_hex).decode(), bytes.fromhex(value_hex).decode()
    except ValueError:
        return None


def resolve_server_variable_groups(
    *group_dicts: Mapping[str, str | None],
) -> ServerVariableMatch | None:
    """Split regex groups into path params and server-variable values/defaults.

    Accepts the host groups then the path groups of one matched row. Returns
    ``None`` when the same variable resolves to different values in the host
    and the path — such a URL is not a consistent instance of the server.
    """
    path_params: dict[str, str] = {}
    values: dict[str, str] = {}
    defaults: dict[str, str] = {}
    for groups in group_dicts:
        for group, captured in groups.items():
            if captured is None:
                continue
            fixed = _decode_fixed_group_name(group)
            if fixed is not None:
                name, value = fixed
            else:
                decoded = _decode_sv_group_name(group)
                if decoded is None:
                    path_params[group] = captured
                    continue
                name, default = decoded
                if captured == "{" + name + "}":
                    if default is not None:
                        defaults[name] = default
                    continue
                value = captured
            existing = values.get(name)
            # Host labels are case-insensitive (the request host is lowercased
            # before matching), so a repeat in host and path compares folded.
            if existing is not None and existing.casefold() != value.casefold():
                return None
            values[name] = value
    for name in values:
        defaults.pop(name, None)
    return ServerVariableMatch(path_params=path_params, values=values, defaults=defaults)


def _raw_host(server_url: str) -> str:
    if "://" not in server_url:
        return ""
    return server_url.split("://", 1)[1].split("/", 1)[0]


def _variable_options(spec: ServerVariableSpec, *, in_host: bool) -> list[_Binding]:
    options: list[_Binding] = []
    if spec.enum:
        members = list(spec.enum)
        if spec.default is not None and spec.default not in members:
            members.append(spec.default)
        options.extend(_Binding(_BindingKind.VALUE, m) for m in members)
        options.append(_Binding(_BindingKind.TEMPLATE))
        return options
    if spec.default is not None:
        options.append(_Binding(_BindingKind.VALUE, spec.default))
    # A free-form host variable stays template-only (see ``_FREE_PATH_VALUE``).
    options.append(_Binding(_BindingKind.TEMPLATE if in_host else _BindingKind.FREE))
    return options


def _fallback_bindings(
    used: list[ServerVariableSpec], host_template: str
) -> list[dict[str, _Binding]]:
    """The capped expansion: the all-defaults combination + the templated one."""

    def _pattern(spec: ServerVariableSpec) -> _Binding:
        in_host = "{" + spec.name + "}" in host_template
        if spec.enum or in_host:
            return _Binding(_BindingKind.TEMPLATE)
        return _Binding(_BindingKind.FREE)

    defaults = {
        s.name: _Binding(_BindingKind.VALUE, s.default) if s.default is not None else _pattern(s)
        for s in used
    }
    templated = {s.name: _pattern(s) for s in used}
    return [defaults, templated] if defaults != templated else [defaults]


def _render_server_template(
    template: str,
    used: list[ServerVariableSpec],
    bindings: Mapping[str, _Binding],
    *,
    in_host: bool,
) -> tuple[str, str]:
    """Render a tokenized host/path into ``(stored pattern, regex body)``."""
    display: list[str] = []
    regex: list[str] = []
    seen_groups: set[str] = set()
    for i, part in enumerate(PATH_PARAM_RE.split(template)):
        if i % 2 == 0:
            display.append(part)
            regex.append(re.escape(part))
            continue
        idx_text = part[len(_SV_TOKEN_PREFIX) :]
        if part.startswith(_SV_TOKEN_PREFIX) and idx_text.isdigit() and int(idx_text) < len(used):
            spec = used[int(idx_text)]
            binding = bindings[spec.name]
            group = _sv_group_name(spec)
            if binding.kind is _BindingKind.VALUE:
                value = binding.value or ""
                shown = value.lower() if in_host else value
                matcher = re.escape(shown)
            elif binding.kind is _BindingKind.TEMPLATE:
                shown = "{" + spec.name + "}"
                matcher = re.escape(shown)
            else:
                shown = "{" + spec.name + "}"
                matcher = _FREE_PATH_VALUE
            display.append(shown)
            if group in seen_groups:
                regex.append(f"(?P={group})")
            else:
                seen_groups.add(group)
                regex.append(f"(?P<{group}>{matcher})")
            continue
        # An undeclared placeholder: legacy handling (host → any label run,
        # path → an operation path parameter).
        display.append("{" + part + "}")
        if in_host:
            regex.append(r"[^:/]+")
        else:
            name, is_catch_all = _split_param_token(part)
            regex.append(f"(?P<{_safe_param_name(name)}>{'.+' if is_catch_all else '[^/]+'})")
    return "".join(display), "".join(regex)


def _entry_for_bindings(
    server_url: str,
    used: list[ServerVariableSpec],
    bindings: Mapping[str, _Binding],
    operation_path: str,
) -> URLIndexEntry:
    tokenized = server_url
    captured: list[ServerVariableSpec] = []
    fixed_groups: list[str] = []
    for spec in used:
        placeholder = "{" + spec.name + "}"
        binding = bindings[spec.name]
        if binding.kind is _BindingKind.VALUE and not binding.value:
            # An empty value cannot be captured (normalization collapses it),
            # so the row pins it with an empty fixed-value group instead.
            tokenized = tokenized.replace(placeholder, "")
            fixed_groups.append(_fixed_value_group(spec.name, ""))
            continue
        tokenized = tokenized.replace(placeholder, "{" + f"{_SV_TOKEN_PREFIX}{len(captured)}" + "}")
        captured.append(spec)

    parsed = parse_server_url(tokenized)
    full_path = normalize_path_template(merge_paths(parsed.path, operation_path))
    host_display, host_regex = _render_server_template(
        parsed.host, captured, bindings, in_host=True
    )
    path_display, path_regex = _render_server_template(full_path, captured, bindings, in_host=False)
    return URLIndexEntry(
        host_pattern=host_display,
        host_regex=re.compile("^" + host_regex + "$", re.IGNORECASE),
        path_pattern=path_display,
        path_regex=re.compile(
            URL_INDEX_FORMAT_MARKER + "^" + "".join(fixed_groups) + path_regex + "$"
        ),
        segment_count=count_segments(path_display),
        param_names=extract_param_names(path_display),
    )


def build_server_index_entries(
    server_url: str, variables: Any, operation_path: str
) -> ServerIndexExpansion:
    """Build every URL-index entry for one server URL + operation path.

    Each declared server variable contributes its options — every enum value
    plus the literal ``{name}`` template for an enum variable; the default plus
    a free-form pattern for an enum-less path variable; the default plus the
    ``{name}`` template for an enum-less host variable — and one entry is built
    per combination (bounded by :data:`MAX_SERVER_VARIABLE_EXPANSIONS`). Every
    server-variable position is a named regex group, so a matched row yields the
    request's concrete variable values (:func:`resolve_server_variable_groups`).
    """
    specs = server_variable_specs(variables)
    used = [s for s in specs if "{" + s.name + "}" in server_url]
    host_template = _raw_host(server_url)

    capped = False
    combos: list[dict[str, _Binding]]
    if not used:
        combos = [{}]
    else:
        option_lists = [
            _variable_options(s, in_host="{" + s.name + "}" in host_template) for s in used
        ]
        if math.prod(len(o) for o in option_lists) > MAX_SERVER_VARIABLE_EXPANSIONS:
            capped = True
            combos = _fallback_bindings(used, host_template)
        else:
            combos = [
                {s.name: b for s, b in zip(used, combo, strict=True)}
                for combo in itertools.product(*option_lists)
            ]

    try:
        entries = [_entry_for_bindings(server_url, used, c, operation_path) for c in combos]
    except ValueError:
        # A variable in an unparseable position (e.g. the port): fall back to
        # the default-expanded URL, as before server variables were expanded.
        # The row pins every expanded variable to its default, so the lookup
        # still resolves (and scopes credentials on) those values.
        expanded = server_url
        fixed: list[str] = []
        for spec in used:
            if spec.default is not None:
                expanded = expanded.replace("{" + spec.name + "}", spec.default)
                fixed.append(_fixed_value_group(spec.name, spec.default))
        parsed = parse_server_url(expanded)
        entry = build_index_entry(
            parsed.host, merge_paths(parsed.path, operation_path), parsed.scheme
        )
        entry.path_regex = re.compile(
            entry.path_regex.pattern.replace("^", "^" + "".join(fixed), 1)
        )
        entries = [entry]
    return ServerIndexExpansion(entries=entries, capped=capped)
