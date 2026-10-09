"""Read an API's server base paths and operation templates (#1424 rule rewrite).

Implements ``shared.permissions.base_path_rewrite.ApiPathShapeReaderProtocol``
over the registry DB, so the control-side rule rewrite never imports the
registry: the composition root (``jentic_one.__main__``) injects this reader.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from jentic_one.registry.core.url_index import (
    PATH_PARAM_RE,
    normalize_path_template,
    server_variable_specs,
)
from jentic_one.registry.repos.api_repo import ApiRepository
from jentic_one.registry.repos.operation_repo import OperationRepository
from jentic_one.shared.context import Context
from jentic_one.shared.permissions.base_path_rewrite import ApiPathShape, BasePath, BasePathKind

_SINGLE_SEGMENT = r"[^/]+"


def server_base_path(server_url: str, variables: Any) -> BasePath | None:
    """``server_url``'s base path as a full-matching regex source, or ``None`` if it has none.

    A declared enum variable becomes the alternation of its members (plus its
    default); any other templated segment matches one path segment. The kind
    is the loosest variable in the base (a one-member enum counts as static:
    it names a single value).
    """
    # urlsplit tolerates ``{host}``/``{port}`` placeholders as long as the
    # port is never read; a relative server URL (``/api/v3``) is all path.
    raw_path = urlsplit(server_url).path if "://" in server_url else server_url
    path = normalize_path_template(raw_path or "/")
    if path == "/":
        return None
    specs = {spec.name: spec for spec in server_variable_specs(variables)}
    kind = BasePathKind.STATIC
    out: list[str] = []
    for i, part in enumerate(PATH_PARAM_RE.split(path)):
        if i % 2 == 0:
            out.append(re.escape(part))
            continue
        spec = specs.get(part)
        if spec is not None and spec.enum:
            members = list(spec.enum)
            if spec.default is not None and spec.default not in members:
                members.append(spec.default)
            out.append("(?:" + "|".join(re.escape(m) for m in members) + ")")
            if len(members) > 1 and kind is BasePathKind.STATIC:
                kind = BasePathKind.ENUM
        else:
            out.append(_SINGLE_SEGMENT)
            kind = BasePathKind.FREEFORM
    return BasePath(pattern="".join(out), kind=kind)


def _shape_from_operations(operations: Sequence[Any]) -> ApiPathShape:
    bases: list[BasePath] = []
    seen_servers: set[str] = set()
    templates: list[str] = []
    for op in operations:
        if op.path not in templates:
            templates.append(op.path)
        for server in list(op.servers or []) + list(op.version_servers or []):
            if server.url in seen_servers:
                continue
            seen_servers.add(server.url)
            base = server_base_path(server.url, server.variables)
            if base is not None and base not in bases:
                bases.append(base)
    return ApiPathShape(base_paths=tuple(bases), operation_templates=tuple(templates))


class RegistryApiPathShapeReader:
    """``ApiPathShapeReaderProtocol`` over the registry DB (live revisions only)."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def get(self, *, vendor: str, name: str, version: str) -> ApiPathShape | None:
        async with self._ctx.registry_db.session() as session:
            api = await ApiRepository.get_by_identifier(session, vendor, name, version)
            if api is None or api.current_revision_id is None:
                return None
            operations = await OperationRepository.list_for_revision(
                session, revision_id=api.current_revision_id
            )
            return _shape_from_operations(operations)
