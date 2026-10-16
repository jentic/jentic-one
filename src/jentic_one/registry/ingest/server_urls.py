"""Resolve relative OpenAPI server URLs against the spec's source URL.

An OpenAPI 3.x server URL may be relative (``/api/v3``): it is then relative to
the location the document is served from. The registry routes requests, scopes
credentials, and builds execute targets by server host, so a relative server
would leave every operation it covers without an upstream host.

Ingest therefore rewrites each relative server to an absolute URL (RFC 3986
reference resolution against the document URL) when the spec has a usable
source URL. Without one, a server list that declares nothing but relative URLs
is refused (:class:`RelativeServerUrlError`); a list that mixes absolute and
relative URLs keeps its absolute entries and drops the relative ones. Every
level is handled the same way: document, path item, and operation ``servers``.

Only declared servers are considered. A spec that declares no ``servers`` keeps
them absent: the OpenAPI default (``/``) would point the API at whatever host
serves the document, which for a mirrored spec is not the API's host.

Server variables are honoured: a URL is relative when it still has no host
after its variables take their ``default``, and the template is resolved with
its ``{name}`` placeholders intact. A template that starts with a variable
whose default is relative (``{basePath}/v1``) has no resolvable template form,
so it counts as unresolvable.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from jentic_one.registry.ingest.exc import RelativeServerUrlError

#: Path-item keys holding an Operation Object (the keys ``servers`` can sit under).
_HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})

_VARIABLE = re.compile(r"\{([^{}]+)\}")


@dataclass
class ServerResolution:
    """What :func:`resolve_relative_servers` changed in the spec."""

    #: ``(relative, absolute)`` pairs, one per rewritten server entry.
    resolved: list[tuple[str, str]] = field(default_factory=list)
    #: Relative URLs removed from a list that still has an absolute server.
    dropped: list[str] = field(default_factory=list)


def server_base_url(source_url: str | None) -> str | None:
    """The base relative server URLs resolve against, or ``None`` when unusable.

    The source URL minus userinfo, query, and fragment: a resolved server URL is
    stored and shown, so it must never carry credentials or a signed query from
    the URL the spec was fetched with.
    """
    if not source_url:
        return None
    try:
        parts = urlsplit(source_url.strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    netloc = f"{host}:{port}" if port is not None else host
    return urlunsplit((parts.scheme, netloc, parts.path or "/", "", ""))


def _expand_defaults(url: str, variables: Any) -> str:
    defaults: dict[str, str] = {}
    if isinstance(variables, Mapping):
        for name, spec in variables.items():
            if isinstance(spec, Mapping) and spec.get("default") is not None:
                defaults[str(name)] = str(spec["default"])
    return _VARIABLE.sub(lambda m: defaults.get(m.group(1), m.group(0)), url)


def is_hostless_server_url(url: str, variables: Any = None) -> bool:
    """Whether a server URL has no host once its variables take their defaults.

    A leading variable that has no default stays unexpanded; the URL is then
    opaque (it may well name a host) and does not count as host-less.
    """
    expanded = _expand_defaults(url, variables).strip()
    if expanded.startswith("{"):
        return False
    try:
        return not urlsplit(expanded).netloc
    except ValueError:
        return False


def _resolve(url: str, base_url: str | None) -> str | None:
    template = url.strip()
    if base_url is None or template.startswith("{"):
        return None
    return urljoin(base_url, template)


def _resolve_list(
    holder: dict[str, Any],
    base_url: str | None,
    result: ServerResolution,
    unresolvable: list[str],
) -> None:
    servers = holder.get("servers")
    if not isinstance(servers, list) or not servers:
        return
    kept: list[Any] = []
    relative: list[str] = []
    changed = False
    for server in servers:
        url = server.get("url") if isinstance(server, Mapping) else None
        if not isinstance(url, str) or not is_hostless_server_url(url, server.get("variables")):
            kept.append(server)
            continue
        changed = True
        absolute = _resolve(url, base_url)
        if absolute is None:
            relative.append(url)
            continue
        kept.append({**server, "url": absolute})
        result.resolved.append((url, absolute))
    if not changed:
        return
    usable = [s for s in kept if isinstance(s, Mapping) and isinstance(s.get("url"), str)]
    if not usable:
        unresolvable.extend(relative)
        return
    result.dropped.extend(relative)
    holder["servers"] = kept


def resolve_relative_servers(content: dict[str, Any], base_url: str | None) -> ServerResolution:
    """Rewrite every relative server in ``content`` to an absolute URL, in place.

    ``base_url`` is the document location (see :func:`server_base_url`), or
    ``None`` when the spec has none. Raises :class:`RelativeServerUrlError`
    naming the offending URLs when any server list is left with no absolute
    server; ``content`` is then not to be ingested.
    """
    result = ServerResolution()
    unresolvable: list[str] = []
    _resolve_list(content, base_url, result, unresolvable)
    paths = content.get("paths")
    if isinstance(paths, Mapping):
        for path_item in paths.values():
            if not isinstance(path_item, dict):
                continue
            _resolve_list(path_item, base_url, result, unresolvable)
            for method, operation in path_item.items():
                if method in _HTTP_METHODS and isinstance(operation, dict):
                    _resolve_list(operation, base_url, result, unresolvable)
    if unresolvable:
        raise RelativeServerUrlError(list(dict.fromkeys(unresolvable)))
    return result
