"""Architecture enforcement: scoped routes hand the caller's identity to their service.

``test_scoped_list_reads.py`` proves service list reads take and use
``identity``; this proves the routes actually pass it. A service method that
takes no identity at all passes every service-side check trivially, so the
route is where an unscoped read or write shows up.

Every route handler (any verb, any path shape) in the ``admin``, ``auth``,
``control`` and ``registry`` routers that takes the caller's identity from
``get_current_identity`` — as a parameter default or inside
``Annotated[Identity, get_current_identity(...)]`` — must pass that identity
into a call on a **service object**. Passing it to a local helper (a rate
limiter, an access check) does not count: the read or write itself has to be
scoped. A service object is:

* a route parameter whose default is ``Depends(get_*_service)``, or whose
  annotation (or ``Annotated[...]`` origin) is a ``*Service`` class;
* a local name assigned from a ``*Service(...)`` constructor call;
* a ``*Service(...)`` constructor called inline (``XService(ctx).get(...)``).

A route that is deliberately not caller-scoped goes in ``_ALLOWLIST`` with the
reason it is safe, and an entry that no longer matches an unscoped route fails
the test. A route handler with an ``Identity`` parameter the walker cannot
recognise as ``get_current_identity`` also fails, and so does a route that
resolves ``get_current_identity`` only through ``dependencies=`` (on the
decorator or its ``APIRouter``) without binding a parameter, so neither a
wrapper, an alias nor a dependency-only gate silently drops a route from the
check.

Routes without a ``get_current_identity`` dependency (health, discovery,
OAuth browser pages) are out of scope here; ``test_web_layer.py`` decides
which routes may be unauthenticated.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from functools import cache
from pathlib import Path

import pytest

from .conftest import SRC_ROOT, python_files_in

SCOPED_WEB_DIRS = [
    SRC_ROOT / "admin" / "web",
    SRC_ROOT / "auth" / "web",
    SRC_ROOT / "control" / "web",
    SRC_ROOT / "registry" / "web",
]

_ROUTE_VERBS = {"get", "post", "put", "patch", "delete"}
_IDENTITY_DEP = "get_current_identity"

_Func = ast.FunctionDef | ast.AsyncFunctionDef

_REGISTRY_CATALOGUE = "org-wide API registry content, not caller-owned; gated by apis:read"
_PUBLIC_CATALOG = "public upstream API catalog, no per-caller rows; gated by capabilities:read"

#: ``<surface>/<module>.<handler>`` -> why it is safe not to pass identity to a service.
_ALLOWLIST: dict[str, str] = {
    # --- admin ---
    "admin/auth.get_current_user": "self-lookup keyed on identity.sub (GET /users/me)",
    "admin/auth.refresh_session": (
        "auth gate only; the service re-derives the user from the presented credential"
    ),
    "admin/config.get_provider_config": (
        "org-wide runtime provider config, secrets redacted; gated by config:read"
    ),
    "admin/config.list_provider_configs": (
        "org-wide runtime provider config, secrets redacted; gated by config:read"
    ),
    "admin/monitoring.get_execution_stats": "org-wide dashboard aggregates; requires org:admin",
    "admin/monitoring.get_usage_stats": "org-wide dashboard aggregates; requires org:admin",
    "admin/oauth_clients.list_oauth_clients": (
        "org-wide OAuth client registry, no owner column; gated by oauth-clients:read"
    ),
    "admin/oauth_clients.get_oauth_client": (
        "org-wide OAuth client registry, no owner column; gated by oauth-clients:read"
    ),
    "admin/permissions.list_permissions": (
        "static permission catalogue; passes identity.sub to compute grantable_by_caller"
    ),
    # --- auth ---
    "auth/identity.get_me": (
        "self-lookup: _resolve_user/_resolve_agent read the caller's own row by identity.sub"
    ),
    "auth/local_login.session_continue_endpoint": (
        "session exchange for the caller's own user row (identity.sub), re-read live"
    ),
    "auth/oauth.introspect_endpoint": (
        "RFC 7662 introspection: the presented token value is the capability, not the caller"
    ),
    "auth/registration.update_registration_endpoint": "always 403: client update unsupported",
    "auth/registration.delete_registration_endpoint": "always 403: client deletion unsupported",
    # --- control ---
    "control/cli_facades.list_cli_facades": (
        "deployment-wide CLI facade addresses read from config, no rows"
    ),
    "control/credentials.list_providers": "static credential-provider metadata, no rows",
    "control/mcp.report_mcp_config_registration": (
        "fire-and-forget telemetry report attributed to identity.sub; reads nothing per-caller"
    ),
    "control/oauth_app_registrations.list_oauth_app_registrations": (
        "org-wide OAuth app registrations, no owner column; requires org:admin"
    ),
    "control/oauth_app_registrations.get_oauth_app_registration": (
        "org-wide OAuth app registrations, no owner column; requires org:admin"
    ),
    "control/vendors.list_vendors": "public vendor registry, no per-caller rows",
    "control/vendors.get_auth_capabilities": "public vendor auth metadata, no per-caller rows",
    # --- registry: the API catalogue is org-wide; only Note is caller-scoped ---
    "registry/apis.list_apis": _REGISTRY_CATALOGUE,
    "registry/apis.get_api": _REGISTRY_CATALOGUE,
    "registry/apis.list_api_security_schemes": _REGISTRY_CATALOGUE,
    "registry/apis.list_api_revisions": _REGISTRY_CATALOGUE,
    "registry/apis.get_api_revision": _REGISTRY_CATALOGUE,
    "registry/apis.list_api_operations": _REGISTRY_CATALOGUE,
    "registry/apis.list_api_revision_operations": _REGISTRY_CATALOGUE,
    "registry/apis.get_api_spec": _REGISTRY_CATALOGUE,
    "registry/apis.get_api_revision_spec": _REGISTRY_CATALOGUE,
    "registry/apis.import_apis": (
        "create-only: enqueues an import job attributed to identity.sub; reads nothing per-caller"
    ),
    "registry/catalog.list_catalog": _PUBLIC_CATALOG,
    "registry/catalog.get_catalog_entry": _PUBLIC_CATALOG,
    "registry/catalog.preview_catalog_operations": _PUBLIC_CATALOG,
    "registry/catalog.refresh_catalog": (
        "org-wide upstream catalog cache refresh; requires org:admin"
    ),
    "registry/inspect.inspect_operation": _REGISTRY_CATALOGUE,
    "registry/overlays.list_overlays": (
        "overlays are shared catalogue content with no owner scoping; writes pass identity"
    ),
    "registry/overlays.get_overlay": (
        "overlays are shared catalogue content with no owner scoping; writes pass identity"
    ),
    "registry/search.search_operations": _REGISTRY_CATALOGUE,
}


# --- AST helpers ---------------------------------------------------------------


def _name_of(node: ast.expr) -> str | None:
    """Trailing identifier of a ``Name`` or ``Attribute`` (``a.b.C`` -> ``C``)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _route_decorators(func: _Func) -> list[ast.Call]:
    """The ``@<router>.<verb>(...)`` decorators on *func*."""
    return [
        dec
        for dec in func.decorator_list
        if isinstance(dec, ast.Call)
        and isinstance(dec.func, ast.Attribute)
        and dec.func.attr in _ROUTE_VERBS
    ]


def _is_route(func: _Func) -> bool:
    return bool(_route_decorators(func))


def _declares_identity_dependency(call: ast.Call) -> bool:
    """Whether *call* has ``dependencies=[..., get_current_identity(...), ...]``."""
    return any(
        kw.arg == "dependencies"
        and isinstance(kw.value, ast.List | ast.Tuple)
        and any(_is_identity_dep(elt) for elt in kw.value.elts)
        for kw in call.keywords
    )


def _identity_gated_routers(tree: ast.Module) -> set[str]:
    """Names bound to ``APIRouter(dependencies=[get_current_identity(...)])`` in *tree*."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and _name_of(node.value.func) == "APIRouter"
            and _declares_identity_dependency(node.value)
        ):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _identity_dependency_without_param(func: _Func, gated_routers: set[str]) -> bool:
    """Whether *func* resolves identity through ``dependencies=`` but binds no parameter.

    Such a route authenticates the caller but has no name to hand the service,
    and ``_identity_param`` returns ``None`` for it, so it would drop out of the
    scoping check.
    """
    if _identity_param(func) is not None:
        return False
    return any(
        _declares_identity_dependency(dec)
        or (isinstance(dec.func, ast.Attribute) and _name_of(dec.func.value) in gated_routers)
        for dec in _route_decorators(func)
    )


def _params(func: _Func) -> Iterator[tuple[ast.arg, ast.expr | None]]:
    """``(arg, default)`` for every positional and keyword-only parameter."""
    positional = func.args.posonlyargs + func.args.args
    pos_defaults: list[ast.expr | None] = [None] * (
        len(positional) - len(func.args.defaults)
    ) + list(func.args.defaults)
    yield from zip(positional, pos_defaults, strict=True)
    yield from zip(func.args.kwonlyargs, func.args.kw_defaults, strict=True)


def _annotated_parts(annotation: ast.expr | None) -> tuple[ast.expr | None, list[ast.expr]]:
    """Split ``Annotated[T, m1, m2]`` into ``(T, [m1, m2])``; anything else is ``(ann, [])``."""
    if (
        isinstance(annotation, ast.Subscript)
        and _name_of(annotation.value) == "Annotated"
        and isinstance(annotation.slice, ast.Tuple)
        and annotation.slice.elts
    ):
        origin, *metadata = annotation.slice.elts
        return origin, metadata
    return annotation, []


def _is_identity_dep(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Call) and _name_of(node.func) == _IDENTITY_DEP


def _is_identity_annotation(annotation: ast.expr | None) -> bool:
    origin, _ = _annotated_parts(annotation)
    return origin is not None and _name_of(origin) == "Identity"


def _identity_param(func: _Func) -> str | None:
    """Name of the parameter resolved by ``get_current_identity(...)``, if any.

    Recognises both ``identity: Identity = get_current_identity(...)`` and
    ``identity: Annotated[Identity, get_current_identity(...)]``.
    """
    for arg, default in _params(func):
        _, metadata = _annotated_parts(arg.annotation)
        if _is_identity_dep(default) or any(_is_identity_dep(m) for m in metadata):
            return arg.arg
    return None


def _is_service_ctor(node: ast.expr) -> bool:
    """``XService(...)`` — a call to a class whose name ends in ``Service``."""
    if not isinstance(node, ast.Call):
        return False
    name = _name_of(node.func)
    return name is not None and name.endswith("Service")


def _is_service_dep(default: ast.expr | None) -> bool:
    """``Depends(get_*_service)``."""
    if not (isinstance(default, ast.Call) and _name_of(default.func) == "Depends"):
        return False
    if not default.args:
        return False
    provider = _name_of(default.args[0])
    return provider is not None and provider.startswith("get") and provider.endswith("_service")


def _service_names(func: _Func) -> set[str]:
    """Parameters and locals in *func* that hold a service object."""
    names: set[str] = set()
    for arg, default in _params(func):
        origin, metadata = _annotated_parts(arg.annotation)
        origin_name = _name_of(origin) if origin is not None else None
        if (
            (origin_name is not None and origin_name.endswith("Service"))
            or _is_service_dep(default)
            or any(_is_service_dep(m) for m in metadata)
        ):
            names.add(arg.arg)
    for node in ast.walk(func):
        if isinstance(node, ast.Assign) and _is_service_ctor(node.value):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif (
            isinstance(node, ast.AnnAssign)
            and node.value is not None
            and _is_service_ctor(node.value)
            and isinstance(node.target, ast.Name)
        ):
            names.add(node.target.id)
    return names


def _passes_to_service(func: _Func, name: str) -> bool:
    """Whether *name* is passed (positionally or by keyword) to a method on a service."""
    services = _service_names(func)
    for node in ast.walk(func):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        receiver = node.func.value
        on_service = (isinstance(receiver, ast.Name) and receiver.id in services) or (
            _is_service_ctor(receiver)
        )
        if not on_service:
            continue
        values = list(node.args) + [kw.value for kw in node.keywords]
        if any(isinstance(v, ast.Name) and v.id == name for v in values):
            return True
    return False


# --- Walkers -------------------------------------------------------------------


@cache
def _parse(py_file: Path) -> ast.Module:
    return ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))


def _route_handlers() -> Iterator[tuple[str, Path, _Func]]:
    """``(key, file, handler)`` for every route handler in the scoped web packages."""
    for web_dir in SCOPED_WEB_DIRS:
        surface = web_dir.parent.name
        for py_file in python_files_in(web_dir):
            for node in ast.walk(_parse(py_file)):
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and _is_route(node):
                    yield f"{surface}/{py_file.stem}.{node.name}", py_file, node


def _unscoped_routes() -> dict[str, tuple[Path, int]]:
    """``key -> (file, line)`` for every authenticated route not passing identity to a service."""
    found: dict[str, tuple[Path, int]] = {}
    for key, py_file, func in _route_handlers():
        identity = _identity_param(func)
        if identity is None or _passes_to_service(func, identity):
            continue
        found[key] = (py_file, func.lineno)
    return found


# --- Tests ---------------------------------------------------------------------


@pytest.mark.arch
def test_scoped_routes_pass_identity_to_their_service() -> None:
    """Every authenticated route passes identity to a service call, or is allowlisted."""
    violations = [
        f"{path}:{line} — {name} takes the caller's identity but never passes it to a "
        "service call; pass `identity=identity` to the service and scope the query with "
        "it (a helper such as a rate limiter or access check does not count), or add the "
        "route to _ALLOWLIST with a reason"
        for name, (path, line) in sorted(_unscoped_routes().items())
        if name not in _ALLOWLIST
    ]
    assert not violations, "Routes not passing identity to a service:\n" + "\n".join(violations)


@pytest.mark.arch
def test_route_identity_allowlist_is_not_stale() -> None:
    """Allowlist entries must name a route that still does not pass identity to a service."""
    unscoped = _unscoped_routes()
    stale = sorted(name for name in _ALLOWLIST if name not in unscoped)
    assert not stale, (
        "Remove stale _ALLOWLIST entries (route gone or now passes identity):\n" + "\n".join(stale)
    )


@pytest.mark.arch
def test_every_identity_parameter_is_recognised() -> None:
    """A route's ``Identity`` parameter must resolve via ``get_current_identity``.

    Otherwise a wrapper or alias around the dependency would make
    ``_identity_param`` return ``None`` and drop the route from the check.
    """
    unrecognised = [
        f"{py_file}:{func.lineno} — {key} has an Identity parameter not resolved by "
        f"{_IDENTITY_DEP}(...) (as a default or in Annotated[Identity, ...]); use it directly "
        "or teach this test the new dependency"
        for key, py_file, func in _route_handlers()
        if any(_is_identity_annotation(arg.annotation) for arg, _ in _params(func))
        and _identity_param(func) is None
    ]
    assert not unrecognised, "Unrecognised identity parameters:\n" + "\n".join(unrecognised)


@pytest.mark.arch
def test_identity_dependency_binds_a_parameter() -> None:
    """A route that resolves identity via ``dependencies=`` must also bind it as a parameter.

    ``dependencies=[get_current_identity(...)]`` on the route decorator or its
    ``APIRouter`` authenticates the caller without giving the handler a name to
    pass to the service, so the route would silently skip the scoping check.
    """
    unbound = [
        f"{py_file}:{func.lineno} — {key} resolves {_IDENTITY_DEP}(...) through "
        "`dependencies=` but takes no identity parameter; declare "
        f"`identity: Identity = {_IDENTITY_DEP}(...)` and pass it to the service"
        for key, py_file, func in _route_handlers()
        if _identity_dependency_without_param(func, _identity_gated_routers(_parse(py_file)))
    ]
    assert not unbound, "Identity resolved without a parameter:\n" + "\n".join(unbound)


@pytest.mark.arch
def test_route_identity_check_finds_routes() -> None:
    """Guard against the walker silently matching nothing (e.g. a decorator rename)."""
    total = sum(1 for _, _, func in _route_handlers() if _identity_param(func) is not None)
    assert total > 50, f"Only {total} authenticated routes found; the route walker is broken"


def _parse_func(source: str) -> _Func:
    node = ast.parse(source).body[0]
    assert isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    return node


@pytest.mark.arch
def test_helper_call_does_not_count_as_passing_identity() -> None:
    """Self-check: identity handed to a helper, not the service, is still unscoped."""
    func = _parse_func(
        "async def r(svc: FooService = Depends(get_foo_service),"
        " identity: Identity = get_current_identity()):\n"
        "    _check(identity)\n"
        "    return await svc.get('x')\n"
    )
    assert _identity_param(func) == "identity"
    assert not _passes_to_service(func, "identity")


@pytest.mark.arch
def test_service_shapes_are_recognised() -> None:
    """Self-check: each recognised service shape counts when it receives identity."""
    shapes = [
        "async def r(svc=Depends(get_foo_service), identity=get_current_identity()):\n"
        "    await svc.get(identity=identity)\n",
        "async def r(svc: FooService, identity: Identity = get_current_identity()):\n"
        "    await svc.get(identity)\n",
        "async def r(ctx, identity: Annotated[Identity, get_current_identity()]):\n"
        "    s = FooService(ctx)\n"
        "    await s.get(identity=identity)\n",
        "async def r(ctx, identity: Identity = get_current_identity()):\n"
        "    await FooService(ctx).get(identity=identity)\n",
    ]
    for source in shapes:
        func = _parse_func(source)
        name = _identity_param(func)
        assert name == "identity", source
        assert _passes_to_service(func, name), source


@pytest.mark.arch
def test_identity_dependency_shapes_are_detected() -> None:
    """Self-check: identity gated via ``dependencies=`` without a parameter is caught."""
    tree = ast.parse(
        "router = APIRouter(dependencies=[get_current_identity()])\n"
        "other = APIRouter()\n"
        "@other.get('/a', dependencies=[get_current_identity()])\n"
        "async def on_route(svc: FooService):\n"
        "    return await svc.get()\n"
        "@router.get('/b')\n"
        "async def on_router(svc: FooService):\n"
        "    return await svc.get()\n"
        "@other.get('/c', dependencies=[get_current_identity()])\n"
        "async def bound(svc: FooService, identity: Identity = get_current_identity()):\n"
        "    return await svc.get(identity=identity)\n"
        "@other.get('/d', dependencies=[Depends(check_rate_limit)])\n"
        "async def unrelated(svc: FooService):\n"
        "    return await svc.get()\n"
    )
    gated = _identity_gated_routers(tree)
    assert gated == {"router"}
    flagged = {
        node.name
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and _identity_dependency_without_param(node, gated)
    }
    assert flagged == {"on_route", "on_router"}
