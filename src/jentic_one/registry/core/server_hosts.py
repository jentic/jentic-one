"""Server host sets — which hosts a revision's servers send requests to.

Used to decide whether a new revision would change *where* bound credentials
are sent (the server-host change guard on catalog re-import and promote). The
set is built from every server a revision declares — API-level and
operation-level, the same two levels ``ExtractServersStage`` persists — so a
spec on disk (``hosts_from_spec``) and a stored revision (``hosts_from_servers``)
are compared on the same footing.

A host is the lowercased ``hostname[:port]`` of an absolute server URL.
Server variables are expanded with their ``default`` and every ``enum`` value,
so a variable that could point the host elsewhere is part of the set.
Relative (hostless) server URLs contribute nothing. The URL scheme is
ignored: an ``http``/``https`` switch on the same host is not a host change.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit

#: Upper bound on variable-expansion combinations per server URL. Past this the
#: unexpanded template is used as-is — still a stable, comparable set member.
_MAX_EXPANSIONS = 64

_VARIABLE = re.compile(r"\{([^{}]+)\}")

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
        values.extend(str(v) for v in enum if v is not None and str(v) not in values)
    return values


def _expand(url: str, variables: Mapping[str, list[str]]) -> list[str]:
    names = [n for n in dict.fromkeys(_VARIABLE.findall(url)) if variables.get(n)]
    if not names:
        return [url]
    choices = [variables[n] for n in names]
    combos = 1
    for c in choices:
        combos *= len(c)
    if combos > _MAX_EXPANSIONS:
        return [url]
    expanded: list[str] = []
    for combo in itertools.product(*choices):
        value = url
        for name, choice in zip(names, combo, strict=True):
            value = value.replace("{" + name + "}", choice)
        expanded.append(value)
    return expanded


def _host_of(url: str) -> str | None:
    candidate = url.strip()
    if "://" not in candidate:
        if not candidate.startswith("//"):
            return None
        candidate = "https:" + candidate
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None
    netloc = parts.netloc.rsplit("@", 1)[-1].lower()
    return netloc or None


def hosts_from_servers(servers: Iterable[ServerSpec]) -> frozenset[str]:
    """Host set of ``(url, variables)`` pairs."""
    hosts: set[str] = set()
    for url, variables in servers:
        for expanded in _expand(url, variables):
            host = _host_of(expanded)
            if host is not None:
                hosts.add(host)
    return frozenset(hosts)


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
    """Host set of a parsed OpenAPI document (API- and operation-level servers)."""
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
