"""Public route and scoping introspection seam for architecture and compliance tests.

Exposes a read-only, structured view of:

- built FastAPI applications across deployment shapes (:func:`build_app`);
- route authentication and permission facts (:class:`RouteFact`, :func:`route_facts`);
- owner-scoped and parent-scoped ORM tables across ``admin``, ``control``, and
  ``registry`` (:class:`ScopedTable`, :func:`scoped_tables`);
- registered control-surface extra access-filter providers
  (:func:`access_filter_providers`);
- served MCP tools, handler bindings, and permission/OAuth-scope metadata
  (:class:`McpToolFact`, :func:`mcp_tool_facts`).
"""

from __future__ import annotations

import atexit
import inspect
import shutil
import tempfile
import weakref
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Literal

from fastapi import FastAPI
from fastapi.routing import APIRoute, _IncludedRouter

from jentic_one.admin.scoping.filters import scoped_model_specs as admin_scoped_model_specs
from jentic_one.broker.web import deps as broker_deps
from jentic_one.composition import build_app as composition_build_app
from jentic_one.composition import build_context as composition_build_context
from jentic_one.control.scoping.filters import (
    AccessFilterProvider,
)
from jentic_one.control.scoping.filters import (
    access_filter_providers as control_access_filter_providers,
)
from jentic_one.control.scoping.filters import (
    scoped_model_specs as control_scoped_model_specs,
)
from jentic_one.mcp.spec import SERVED_TOOLS
from jentic_one.mcp.tools import HANDLERS as MCP_HANDLERS
from jentic_one.registry.scoping.filters import scoped_model_specs as registry_scoped_model_specs
from jentic_one.shared.auth.permission_catalog import (
    BROKER_EXECUTE_PERMISSION,
    MCP_TOOL_SCOPES,
)
from jentic_one.shared.config import AppConfig, load_config
from jentic_one.shared.context import Context
from jentic_one.shared.models.actors import ActorType
from jentic_one.shared.web import static as static_module
from jentic_one.shared.web.endpoint_permissions import (
    ACTOR_TYPE_OVERRIDES,
    NON_IDENTITY_AUTH,
    PATH_PERMISSION_OVERRIDES,
    _closure_values,
    _is_identity_dependency,
    _normalise_path,
    build_operation_auth_map,
)
from jentic_one.shared.web.openapi_meta import generate_operation_id

__all__ = [
    "INTROSPECTION_SCHEMA",
    "McpToolFact",
    "RouteFact",
    "ScopedTable",
    "access_filter_providers",
    "build_app",
    "mcp_tool_facts",
    "route_facts",
    "scoped_tables",
]

INTROSPECTION_SCHEMA: Final = "jentic.introspect/v1"

_DEFAULT_CONFIG_PATH: Final = Path(__file__).resolve().parents[3] / "config" / "local-sqlite.yaml"

_ALL_ACTOR_TYPES: Final[frozenset[str]] = frozenset(a.value for a in ActorType)

#: Broker identity dependencies. The broker validates credentials with its own
#: composite token validator rather than ``get_current_identity``; each of these
#: calls ``require_broker_identity`` (401 on a missing/invalid credential).
_BROKER_AUTH_DEPENDENCIES: Final[tuple[Callable[..., Any], ...]] = (
    broker_deps.require_broker_identity,
    broker_deps.require_execute_permission,
    broker_deps.require_execute_within_rate_limit,
)

_BROKER_EXECUTE_DEPENDENCIES: Final[tuple[Callable[..., Any], ...]] = (
    broker_deps.require_execute_permission,
    broker_deps.require_execute_within_rate_limit,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class RouteFact:
    """Normalized authentication and permission facts for one route on a built app."""

    method: str
    path: str
    operation_id: str | None
    module: str
    authenticated: bool
    permissions: frozenset[str]
    actor_types: frozenset[str]
    in_schema: bool
    auth_via: str | None
    opaque: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class McpToolFact:
    """Normalized handler and permission-scope metadata for one served MCP tool."""

    name: str
    has_handler: bool
    module: str | None
    permissions: frozenset[str]
    scopes: frozenset[str]


#: Permission gates enforced by each served MCP tool handler (or its broker hop).
_MCP_TOOL_PERMISSIONS: Final[Mapping[str, frozenset[str]]] = {
    "whoami": frozenset(),
    "search_apis": frozenset({"apis:read"}),
    "inspect_operation": frozenset({"apis:read"}),
    "execute": frozenset({BROKER_EXECUTE_PERMISSION}),
    "execute_read": frozenset({BROKER_EXECUTE_PERMISSION}),
    "get_execution_result": frozenset({"jobs:read"}),
    "search_catalog": frozenset({"capabilities:read"}),
    "import_api": frozenset({"catalog:import"}),
    "inspect_api": frozenset({"credentials:connect", "credentials:write"}),
    "request_connection": frozenset({"credentials:connect", "credentials:write"}),
    "show_access": frozenset({"credentials:read", "owner:credentials:read"}),
    "request_access_change": frozenset({"credentials:connect", "credentials:write"}),
    "amend_request": frozenset({"credentials:connect", "credentials:write"}),
}


@dataclass(frozen=True, slots=True, kw_only=True)
class ScopedTable:
    """Normalized scoping metadata for one owner- or parent-scoped ORM table."""

    surface: Literal["admin", "control", "registry"]
    table: str
    model: str
    owner_columns: tuple[str, ...]
    id_column: str | None
    delegation_permission: str | None
    owned_agent_axis: bool
    parent: str | None = None


_PARENT_SCOPED_TABLES: Final[tuple[ScopedTable, ...]] = (
    ScopedTable(
        surface="admin",
        table="agent_credential_bindings",
        model="jentic_one.admin.core.schema.agent_credential_bindings.AgentCredentialBinding",
        owner_columns=("agent_id", "credential_id"),
        id_column=None,
        delegation_permission=None,
        owned_agent_axis=False,
        parent="agent",
    ),
    ScopedTable(
        surface="control",
        table="agent_permission_rules",
        model="jentic_one.control.core.schema.agent_permission_rules.AgentPermissionRule",
        owner_columns=("agent_id", "credential_id"),
        id_column=None,
        delegation_permission=None,
        owned_agent_axis=False,
        parent="agent",
    ),
    ScopedTable(
        surface="admin",
        table="job_results",
        model="jentic_one.admin.core.schema.job_results.JobResult",
        owner_columns=("job_id",),
        id_column=None,
        delegation_permission=None,
        owned_agent_axis=False,
        parent="job",
    ),
)


def _make_spa_stub_dir() -> Path:
    bundle = Path(tempfile.mkdtemp(prefix="jentic-spa-stub-"))
    atexit.register(shutil.rmtree, bundle, True)
    (bundle / "index.html").write_text("<!doctype html>", encoding="utf-8")
    return bundle


def _dispose_context_sync(ctx: Context) -> None:
    """Dispose any SQLAlchemy engines already constructed on ``ctx``."""
    for db_attr in ("_control_db", "_admin_db", "_registry_db"):
        db = getattr(ctx, db_attr, None)
        if db is None:
            continue
        for engine_attr in ("_engine", "engine"):
            if isinstance(getattr(type(db), engine_attr, None), property):
                continue
            engine = getattr(db, engine_attr, None)
            if engine is not None and hasattr(engine, "sync_engine"):
                engine.sync_engine.dispose()
                break


def _attach_context_cleanup(app: FastAPI, ctx: Context) -> FastAPI:
    async def _shutdown_context() -> None:
        await ctx.shutdown()
        await ctx.close_state_backend()

    app.router.on_shutdown.append(_shutdown_context)
    weakref.finalize(app, _dispose_context_sync, ctx)
    return app


def build_app(
    apps: Sequence[str],
    *,
    config: AppConfig | None = None,
    spa_stub: bool = True,
) -> FastAPI:
    """Build a FastAPI app for ``apps`` through the standard composition root.

    When ``config`` is ``None``, loads ``config/local-sqlite.yaml`` from the
    repository checkout (falling back to :func:`load_config` when outside a
    repo tree). When ``spa_stub`` is ``True``, stubs ``_resolve_static_dir``
    with a minimal ``index.html`` directory so SPA shell routes mount even when
    the UI bundle has not been built.
    """
    resolved_config = (
        config
        if config is not None
        else (
            load_config(_DEFAULT_CONFIG_PATH) if _DEFAULT_CONFIG_PATH.is_file() else load_config()
        )
    )
    app_list = list(apps)
    if list(resolved_config.apps) != app_list:
        resolved_config = resolved_config.model_copy(update={"apps": app_list})
    ctx = composition_build_context(resolved_config, app_list)
    if not spa_stub:
        return _attach_context_cleanup(composition_build_app(ctx, app_list), ctx)
    bundle = _make_spa_stub_dir()
    original_resolver = static_module._resolve_static_dir
    static_module._resolve_static_dir = lambda: bundle
    try:
        return _attach_context_cleanup(composition_build_app(ctx, app_list), ctx)
    finally:
        static_module._resolve_static_dir = original_resolver


def _walk_routes(
    routes: Sequence[Any],
    prefix: str = "",
    include_deps: tuple[Any, ...] = (),
    in_schema: bool = True,
) -> Iterator[tuple[str, Any, tuple[Any, ...], bool]]:
    """Flatten ``app.routes``, threading include-level prefix, dependencies, and schema flags."""
    for route in routes:
        if isinstance(route, _IncludedRouter):
            context = route.include_context
            yield from _walk_routes(
                route.original_router.routes,
                prefix + context.prefix,
                include_deps + tuple(d.dependency for d in context.dependencies),
                in_schema and bool(context.include_in_schema),
            )
        else:
            yield prefix, route, include_deps, in_schema


def _dependency_calls(route: APIRoute) -> Iterator[Any]:
    stack = list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        yield dep.call
        stack.extend(dep.dependencies)


def _auth_via(calls: Sequence[Any], key: tuple[str, str]) -> str | None:
    if any(_is_identity_dependency(call) for call in calls):
        return "get_current_identity"
    if any(call is dep for call in calls for dep in _BROKER_AUTH_DEPENDENCIES):
        return "broker identity dependency"
    if key in NON_IDENTITY_AUTH:
        return "NON_IDENTITY_AUTH"
    return None


def _endpoint_module(endpoint: Any, fallback: Any) -> str:
    target = inspect.unwrap(endpoint) if callable(endpoint) else endpoint
    if target is None:
        return type(fallback).__module__
    mod = getattr(target, "__module__", None)
    if isinstance(mod, str) and mod:
        return mod
    return type(target).__module__


def _extract_identity_closure(
    calls: Sequence[Any],
) -> tuple[bool, list[str] | None, frozenset[str] | None]:
    has_identity = False
    perms: list[str] = []
    allowed_actors: frozenset[str] | None = None
    for call in calls:
        is_identity, d_perms, d_actor = _closure_values(call)
        if is_identity:
            has_identity = True
            if d_perms:
                for perm in d_perms:
                    if perm not in perms:
                        perms.append(perm)
            if d_actor is not None:
                actor_set = frozenset({d_actor.value})
                allowed_actors = (
                    actor_set if allowed_actors is None else (allowed_actors & actor_set)
                )
    return has_identity, (perms or None), allowed_actors


def route_facts(app: FastAPI) -> tuple[RouteFact, ...]:
    """Return deterministic, deduplicated route facts for every route on ``app``."""
    spec_ids: dict[tuple[str, str], str] = {}
    for path, item in app.openapi().get("paths", {}).items():
        if not isinstance(item, dict):
            continue
        for method, operation in item.items():
            if isinstance(operation, dict) and "operationId" in operation:
                spec_ids[(method.upper(), path)] = operation["operationId"]

    auth_map = build_operation_auth_map(app)
    seen: dict[tuple[str, str], RouteFact] = {}

    for prefix, route, include_deps, include_in_schema in _walk_routes(app.routes):
        path = _normalise_path(prefix + getattr(route, "path", ""))
        if not isinstance(route, APIRoute):
            endpoint = getattr(route, "endpoint", None) or getattr(route, "app", None)
            raw_methods = getattr(route, "methods", None)
            methods = sorted(
                m.upper() for m in (raw_methods or {"*"}) if m.upper() not in {"HEAD", "OPTIONS"}
            )
            module = _endpoint_module(endpoint, route)
            for method in methods:
                opaque_key = (method, path)
                seen.setdefault(
                    opaque_key,
                    RouteFact(
                        method=method,
                        path=path,
                        operation_id=None,
                        module=module,
                        authenticated=False,
                        permissions=frozenset(),
                        actor_types=frozenset(),
                        in_schema=False,
                        auth_via=None,
                        opaque=True,
                    ),
                )
            continue

        calls = [*include_deps, *_dependency_calls(route)]
        in_schema = include_in_schema and route.include_in_schema
        module = _endpoint_module(route.endpoint, route)
        _, closure_perms, closure_actors = _extract_identity_closure(calls)

        for method in sorted((route.methods or set()) - {"HEAD", "OPTIONS"}):
            key = (method, path)
            op_id = spec_ids.get(key) if in_schema else None
            op_id = op_id or route.operation_id or generate_operation_id(route)
            auth_via = _auth_via(calls, key)
            info = auth_map.get(key)

            if auth_via == "broker identity dependency":
                authenticated = True
                has_exec_perm = any(
                    call is dep for call in calls for dep in _BROKER_EXECUTE_DEPENDENCIES
                )
                permissions = (
                    frozenset({BROKER_EXECUTE_PERMISSION}) if has_exec_perm else frozenset()
                )
                actor_types = frozenset({"agent"})
            elif auth_via == "get_current_identity":
                authenticated = True
                permissions = frozenset(PATH_PERMISSION_OVERRIDES.get(key, closure_perms or []))
                if key in ACTOR_TYPE_OVERRIDES:
                    actor_types = frozenset(ACTOR_TYPE_OVERRIDES[key])
                elif closure_actors is not None:
                    actor_types = closure_actors
                else:
                    actor_types = _ALL_ACTOR_TYPES
            elif info is not None and info["authenticated"]:
                authenticated = True
                permissions = frozenset(info["permissions"])
                actor_types = frozenset(info["actor_types"])
            elif auth_via is not None:
                authenticated = True
                permissions = frozenset(info["permissions"]) if info else frozenset()
                actor_types = frozenset(info["actor_types"]) if info else _ALL_ACTOR_TYPES
            else:
                authenticated = False
                permissions = frozenset()
                actor_types = frozenset()

            seen.setdefault(
                key,
                RouteFact(
                    method=method,
                    path=path,
                    operation_id=op_id,
                    module=module,
                    authenticated=authenticated,
                    permissions=permissions,
                    actor_types=actor_types,
                    in_schema=in_schema,
                    auth_via=auth_via,
                    opaque=False,
                ),
            )

    return tuple(sorted(seen.values(), key=lambda f: (f.path, f.method)))


def scoped_tables() -> Mapping[str, ScopedTable]:
    """Return a read-only mapping of ``"<surface>.<table>"`` to :class:`ScopedTable`."""
    entries: dict[str, ScopedTable] = {}
    surface_specs: tuple[tuple[Literal["admin", "control", "registry"], Sequence[Any]], ...] = (
        ("admin", admin_scoped_model_specs()),
        ("control", control_scoped_model_specs()),
        ("registry", registry_scoped_model_specs()),
    )
    for surface, specs in surface_specs:
        for spec in specs:
            key = f"{surface}.{spec.table}"
            entries[key] = ScopedTable(
                surface=surface,
                table=spec.table,
                model=spec.model,
                owner_columns=spec.owner_columns,
                id_column=spec.id_column,
                delegation_permission=spec.delegation_permission,
                owned_agent_axis=spec.owned_agent_axis,
                parent=None,
            )
    for child in _PARENT_SCOPED_TABLES:
        entries[f"{child.surface}.{child.table}"] = child
    return MappingProxyType(dict(sorted(entries.items())))


def access_filter_providers() -> tuple[AccessFilterProvider, ...]:
    """Return a read-only snapshot of registered control-surface access-filter providers."""
    return control_access_filter_providers()


def mcp_tool_facts() -> tuple[McpToolFact, ...]:
    """Return deterministic metadata for every served MCP tool."""
    facts: list[McpToolFact] = []
    for name in SERVED_TOOLS:
        handler = MCP_HANDLERS.get(name)
        has_handler = handler is not None and callable(handler)
        module = _endpoint_module(handler, handler) if handler is not None else None
        permissions = _MCP_TOOL_PERMISSIONS.get(name, frozenset())
        facts.append(
            McpToolFact(
                name=name,
                has_handler=has_handler,
                module=module,
                permissions=permissions,
                scopes=permissions & MCP_TOOL_SCOPES,
            )
        )
    return tuple(facts)
