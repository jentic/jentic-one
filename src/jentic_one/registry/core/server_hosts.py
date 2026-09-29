"""Server origin sets — which hosts, over which schemes, a revision's servers target.

Used to decide whether a new revision would change *where* (or *how*) bound
credentials are sent: the server-host change guard on catalog re-import and
promote. The set is built from every server a revision declares — API-level and
operation-level, the same two levels ``ExtractServersStage`` persists — so a
spec on disk (``hosts_from_spec``) and a stored revision (``hosts_from_servers``)
are compared on the same footing.

Each member is a normalised origin, ``scheme://host[:port]``:

- scheme and host are lowercased, a trailing dot on the host is dropped,
  userinfo is dropped, and the scheme's default port (``:443`` for https,
  ``:80`` for http) is dropped;
- a protocol-relative (``//host``) or non-http(s)/templated scheme yields both
  the ``http`` and ``https`` origin, so it counts as reachable over plaintext;
- a relative, hostless server URL (``/v1``) contributes nothing: the broker's
  URL index keys on the host and never routes a hostless server, so it cannot
  carry a credential anywhere. A switch from an absolute server to a relative
  one still changes the set and is caught. A relative-looking template that
  still has an unresolved variable (``{base}/v1``) is kept as an opaque member.

Server variables that appear in the scheme or authority are expanded with their
``default`` and every ``enum`` value, so a variable that could point the host
elsewhere is part of the set. Variables that only appear in the path cannot
change the host and are left alone. Past ``_MAX_EXPANSIONS`` combinations the
server contributes a single canonical member built from its template *and*
every candidate value, so any change to those values still changes the set:
the cap can only produce a spurious hold, never hide a change.

This module is self-contained (no dependency on ``url_index``) so it can evolve
independently of the URL-index builder.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterable, Mapping
from typing import Any

#: Upper bound on variable-expansion combinations per server URL. Past this the
#: server contributes one canonical, value-sensitive member (see module docstring).
_MAX_EXPANSIONS = 64

_VARIABLE = re.compile(r"\{([^{}]+)\}")

_DEFAULT_PORTS = {"http": "80", "https": "443"}
_WEB_SCHEMES = ("http", "https")

#: Characters that end a URL authority. ``\`` is included because WHATWG URL
#: parsers treat it as ``/`` for http(s), so ``https://a.example\@b.example``
#: targets ``a.example``.
_AUTHORITY_END = re.compile(r"[/?#\\]")

#: One server as ``(url, {variable_name: [candidate values]})``.
ServerSpec = tuple[str, Mapping[str, list[str]]]


def _variable_values(variable: Any) -> list[str]:
    """Candidate values of one OpenAPI server variable: default first, then enum."""
    if not isinstance(variable, Mapping):
        return []
    values: list[str] = []
    default = variable.get("default")
    if default is not None:
        values.append(str(default))
    enum = variable.get("enum")
    if isinstance(enum, list):
        for v in enum:
            if v is not None and str(v) not in values:
                values.append(str(v))
    return values


def _origin_prefix(url: str) -> str:
    """The part of a server URL template that can decide scheme and host.

    ``scheme://authority`` for an absolute template. A template without ``://``
    (e.g. ``{base}/v1``) is returned whole: a variable anywhere in it could
    still supply a scheme and host.
    """
    marker = url.find("://")
    if marker < 0:
        return url
    start = marker + 3
    end = _AUTHORITY_END.search(url, start)
    return url if end is None else url[: end.start()]


def _expand(url: str, variables: Mapping[str, list[str]]) -> tuple[list[str], set[str]]:
    """Scheme/host-relevant expansions of ``url`` (bounded, see module docstring).

    Returns ``(urls, opaque)``: concrete URLs to parse, plus opaque origin
    members standing in for a server whose expansion exceeds the cap.
    """
    prefix = _origin_prefix(url)
    names = [n for n in dict.fromkeys(_VARIABLE.findall(prefix)) if variables.get(n)]
    if not names:
        return [url], set()
    choices = [variables[n] for n in names]
    combos = 1
    for c in choices:
        combos *= len(c)
    if combos > _MAX_EXPANSIONS:
        return [], _capped_members(prefix, names, variables)
    expanded: list[str] = []
    for combo in itertools.product(*choices):
        value = url
        for name, choice in zip(names, combo, strict=True):
            value = value.replace("{" + name + "}", choice)
        expanded.append(value)
    return expanded, set()


def _capped_members(prefix: str, names: list[str], variables: Mapping[str, list[str]]) -> set[str]:
    """Canonical stand-ins for a server whose expansion exceeds the cap.

    Built from the unexpanded ``scheme://authority`` template plus every
    candidate value, so two servers only compare equal when their templates and
    value sets are identical. Emitted for ``http`` too unless the template
    pins ``https``, so the plaintext rule in ``needs_review`` stays fail-closed.
    """
    described = ";".join(
        f"{name}={'|'.join(sorted(set(variables[name])))}" for name in sorted(names)
    )
    marker = prefix.find("://")
    scheme = prefix[:marker].lower() if marker >= 0 else ""
    authority = prefix[marker + 3 :] if marker >= 0 else prefix
    schemes = (scheme,) if scheme in _WEB_SCHEMES else _WEB_SCHEMES
    return {f"{s}://{authority.lower()}[{described}]" for s in schemes}


def _normalise_authority(authority: str, scheme: str) -> str | None:
    """``host[:port]`` of a URL authority: userinfo dropped, default port dropped."""
    authority = authority.rsplit("@", 1)[-1].strip().lower()
    if not authority:
        return None
    host, port = authority, ""
    if authority.startswith("["):
        close = authority.find("]")
        if close > 0:
            host = authority[: close + 1]
            rest = authority[close + 1 :]
            port = rest[1:] if rest.startswith(":") else ""
    elif authority.count(":") == 1:
        host, port = authority.split(":", 1)
    host = host.rstrip(".")
    if not host:
        return None
    if port and port != _DEFAULT_PORTS.get(scheme):
        return f"{host}:{port}"
    return host


def _origins_of(url: str) -> set[str]:
    """Normalised origins one (expanded) server URL targets; empty when hostless."""
    candidate = url.strip()
    if candidate.startswith("//"):
        scheme_part, rest = "", candidate[2:]
    else:
        marker = candidate.find("://")
        if marker < 0:
            if _VARIABLE.search(candidate):
                # An unresolved template could still supply a scheme and host
                # (e.g. from a credential's server variables): keep it opaque.
                return {f"{s}://{candidate.lower()}" for s in _WEB_SCHEMES}
            return set()
        scheme_part, rest = candidate[:marker].lower(), candidate[marker + 3 :]
    end = _AUTHORITY_END.search(rest)
    authority = rest if end is None else rest[: end.start()]
    # Protocol-relative, templated or non-web schemes: assume either scheme.
    schemes = (scheme_part,) if scheme_part in _WEB_SCHEMES else _WEB_SCHEMES
    origins: set[str] = set()
    for scheme in schemes:
        host = _normalise_authority(authority, scheme)
        if host is not None:
            origins.add(f"{scheme}://{host}")
    return origins


def hosts_from_servers(servers: Iterable[ServerSpec]) -> frozenset[str]:
    """Origin set of ``(url, variables)`` pairs."""
    origins: set[str] = set()
    for url, variables in servers:
        expanded, opaque = _expand(url, variables)
        origins |= opaque
        for concrete in expanded:
            origins |= _origins_of(concrete)
    return frozenset(origins)


def _server_specs(servers: Any) -> list[ServerSpec]:
    specs: list[ServerSpec] = []
    if not isinstance(servers, list):
        return specs
    for server in servers:
        if not isinstance(server, Mapping):
            continue
        url = server.get("url")
        if not isinstance(url, str):
            continue
        raw_vars = server.get("variables")
        variables = (
            {str(k): _variable_values(v) for k, v in raw_vars.items()}
            if isinstance(raw_vars, Mapping)
            else {}
        )
        specs.append((url, variables))
    return specs


def hosts_from_spec(content: Mapping[str, Any] | None) -> frozenset[str]:
    """Origin set of a parsed OpenAPI document (API- and operation-level servers)."""
    if not content:
        return frozenset()
    specs = _server_specs(content.get("servers"))
    paths = content.get("paths")
    if isinstance(paths, Mapping):
        for path_item in paths.values():
            if not isinstance(path_item, Mapping):
                continue
            for operation in path_item.values():
                if isinstance(operation, Mapping):
                    specs.extend(_server_specs(operation.get("servers")))
    return hosts_from_servers(specs)


def _hosts(origins: frozenset[str]) -> set[str]:
    return {origin.split("://", 1)[1] for origin in origins}


def _plaintext_hosts(origins: frozenset[str]) -> set[str]:
    return {origin.split("://", 1)[1] for origin in origins if origin.startswith("http://")}


def needs_review(current: frozenset[str], new: frozenset[str]) -> bool:
    """True when moving from ``current`` to ``new`` origins needs an operator.

    That is when the host set changes, or when a host becomes reachable over
    plaintext ``http`` that was only reachable over ``https`` before (a
    downgrade would send stored credentials in cleartext). Moving a host from
    ``http`` to ``https`` alone does not need review.
    """
    if _hosts(current) != _hosts(new):
        return True
    return bool(_plaintext_hosts(new) - _plaintext_hosts(current))
