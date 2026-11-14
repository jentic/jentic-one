"""Architecture enforcement: scoped routes hand the caller's identity to their service.

``test_scoped_list_reads.py`` proves service list reads take and use
``identity``; this proves the routes actually pass it. A service method that
takes no identity at all passes every service-side check trivially, so the
route is where an unscoped read or write shows up.

Every route handler (any verb, any path shape) in the ``admin``, ``auth``,
``broker``, ``control`` and ``registry`` routers that takes the caller's identity from
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
from string import Formatter
from typing import NamedTuple

import pytest

from .conftest import SRC_ROOT, python_files_in

SCOPED_WEB_DIRS = [
    SRC_ROOT / "admin" / "web",
    SRC_ROOT / "auth" / "web",
    SRC_ROOT / "broker" / "web",
    SRC_ROOT / "control" / "web",
    SRC_ROOT / "registry" / "web",
]

_ROUTE_VERBS = {"get", "post", "put", "patch", "delete", "api_route"}
_IDENTITY_DEP = "get_current_identity"
_IDENTITY_PARAM_TYPES = {
    "CurrentIdentity",
    "RequireBrokerIdentity",
    "RequireExecuteAccess",
    "RequireJobAccess",
}
_SERVICE_PARAM_TYPES = {"BrokerJobServiceDep"}

_Func = ast.FunctionDef | ast.AsyncFunctionDef


class _ByIdCall(NamedTuple):
    route: str
    path: Path
    line: int
    method: str
    parameters: frozenset[str]
    post_authorized: bool

    @property
    def key(self) -> str:
        return f"{self.route} -> {self.method}"


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
    # --- broker ---
    "broker/execute.proxy": (
        "_handle forwards the same identity into execution authorization and credential resolution"
    ),
    # --- control ---
    "control/credentials.list_providers": "static credential-provider metadata, no rows",
    "control/integrations.integrations_connect_options": (
        "an API's declared schemes and the org's connect source for it, no caller-owned rows"
    ),
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

#: ``<surface>/<module>.<handler> -> <method>`` -> why an earlier scoped load is sufficient.
_POST_AUTHORIZED: dict[str, str] = {
    "control/credentials.list_agent_permissions -> scope_options": (
        "get_binding_access first authorizes credential_id with identity; scope_options reads "
        "only that credential's public OAuth scope catalogue"
    ),
}

#: The only call that post-authorizes each exempted ``scope_options`` occurrence:
#: ``key -> (authorizer method, required path parameter)``.
_POST_AUTHORIZED_AUTHORIZERS: dict[str, tuple[str, str]] = {
    "control/credentials.list_agent_permissions -> scope_options": (
        "get_binding_access",
        "credential_id",
    ),
}

#: ``<surface>/<module>.<handler> -> <method>`` -> why this org-wide call needs no identity.
_BY_ID_CALL_ALLOWLIST: dict[str, str] = {
    "registry/apis.update_api -> get_by_identity": (
        "an empty PATCH reads org-wide API registry content with no caller-owned rows; "
        "the route still requires apis:write"
    ),
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


def _declares_identity_dependency(call: ast.Call, tree: ast.Module | None = None) -> bool:
    """Whether *call* has ``dependencies=[..., get_current_identity(...), ...]``."""
    return any(
        kw.arg == "dependencies"
        and isinstance(kw.value, ast.List | ast.Tuple)
        and any(_is_identity_dep(elt, tree) for elt in kw.value.elts)
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
            and _declares_identity_dependency(node.value, tree)
        ):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _identity_dependency_without_param(
    func: _Func, gated_routers: set[str], tree: ast.Module | None = None
) -> bool:
    """Whether *func* resolves identity through ``dependencies=`` but binds no parameter.

    Such a route authenticates the caller but has no name to hand the service,
    and ``_identity_param`` returns ``None`` for it, so it would drop out of the
    scoping check.
    """
    if _identity_param(func, tree) is not None:
        return False
    return any(
        _declares_identity_dependency(dec, tree)
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


def _import_aliases(tree: ast.Module | None, imported_names: set[str]) -> set[str]:
    if tree is None:
        return set()
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            aliases.update(
                alias.asname or alias.name for alias in node.names if alias.name in imported_names
            )
        elif (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Name)
            and node.value.id in imported_names
        ):
            aliases.update(target.id for target in node.targets if isinstance(target, ast.Name))
    return aliases


def _dependency_aliases(tree: ast.Module | None) -> set[str]:
    return {_IDENTITY_DEP} | _import_aliases(tree, {_IDENTITY_DEP})


def _identity_param_types(tree: ast.Module | None) -> set[str]:
    names = _IDENTITY_PARAM_TYPES | _import_aliases(tree, _IDENTITY_PARAM_TYPES)
    if tree is not None:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            origin, _ = _annotated_parts(node.value)
            if isinstance(origin, ast.Name) and origin.id == "Identity":
                names.update(target.id for target in node.targets if isinstance(target, ast.Name))
    return names


def _is_identity_dep(node: ast.expr | None, tree: ast.Module | None = None) -> bool:
    return isinstance(node, ast.Call) and _name_of(node.func) in _dependency_aliases(tree)


def _is_identity_annotation(annotation: ast.expr | None, tree: ast.Module | None = None) -> bool:
    origin, _ = _annotated_parts(annotation)
    name = _name_of(origin) if origin is not None else None
    return name == "Identity" or name in _identity_param_types(tree)


def _identity_param(func: _Func, tree: ast.Module | None = None) -> str | None:
    """Name of the parameter resolved by a recognised identity dependency, if any.

    Recognises direct dependencies, their import aliases, and aliases whose
    ``Annotated`` origin is ``Identity``.
    """
    identity_types = _identity_param_types(tree)
    for arg, default in _params(func):
        origin, metadata = _annotated_parts(arg.annotation)
        origin_name = _name_of(origin) if origin is not None else None
        if (
            _is_identity_dep(default, tree)
            or any(_is_identity_dep(m, tree) for m in metadata)
            or origin_name in identity_types
        ):
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
            or origin_name in _SERVICE_PARAM_TYPES
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


def _path_parameters(func: _Func) -> frozenset[str]:
    """Path id parameter names declared by this handler's route decorators."""
    names: set[str] = set()
    for decorator in _route_decorators(func):
        if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
            continue
        path = decorator.args[0].value
        if not isinstance(path, str):
            continue
        for _, field_name, _, _ in Formatter().parse(path):
            if field_name:
                names.add(field_name)
    return frozenset(names)


def _call_argument_names(call: ast.Call) -> frozenset[str]:
    names: set[str] = set()
    for value in list(call.args) + [kw.value for kw in call.keywords]:
        for node in ast.walk(value):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
    return frozenset(names)


def _binds_parameter(call: ast.Call, parameter: str, position: int = 0) -> bool:
    """Whether *parameter* is passed by its documented position or matching keyword."""
    if len(call.args) > position:
        value = call.args[position]
        if isinstance(value, ast.Name) and value.id == parameter:
            return True
    return any(
        kw.arg == parameter and isinstance(kw.value, ast.Name) and kw.value.id == parameter
        for kw in call.keywords
    )


def _by_id_service_calls(
    route: str, py_file: Path, func: _Func, tree: ast.Module | None = None
) -> list[_ByIdCall]:
    """Service calls using a path resource key without identity in that same call."""
    identity = _identity_param(func, tree)
    if identity is None:
        return []
    path_parameters = _path_parameters(func)
    services = _service_names(func)
    # Each exempted occurrence is authorized only by its documented authorizer,
    # not by any earlier identity-bearing call. key -> (method, path parameter).
    authorizers = {
        key: spec
        for key, spec in _POST_AUTHORIZED_AUTHORIZERS.items()
        if key.startswith(f"{route} ")
    }
    authorized: dict[str, set[str]] = {key: set() for key in authorizers}
    calls: list[_ByIdCall] = []
    service_calls = sorted(
        (
            (node, node.func)
            for node in ast.walk(func)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ),
        key=lambda item: (item[0].lineno, item[0].col_offset),
    )
    for node, call_func in service_calls:
        receiver = call_func.value
        on_service = (isinstance(receiver, ast.Name) and receiver.id in services) or (
            _is_service_ctor(receiver)
        )
        if not on_service:
            continue
        arguments = _call_argument_names(node)
        used_path_parameters = arguments & path_parameters
        if not used_path_parameters:
            continue
        values = list(node.args) + [kw.value for kw in node.keywords]
        has_identity = any(isinstance(value, ast.Name) and value.id == identity for value in values)
        # Only the documented authorizer, carrying identity and its path parameter,
        # marks the matching exempted occurrence as post-authorized.
        for key, (method, parameter) in authorizers.items():
            if call_func.attr == method and has_identity and _binds_parameter(node, parameter):
                authorized[key].add(parameter)
        if has_identity:
            continue
        key = f"{route} -> {call_func.attr}"
        post_authorized = bool(
            authorized.get(key) and used_path_parameters.issubset(authorized[key])
        )
        calls.append(
            _ByIdCall(
                route=route,
                path=py_file,
                line=node.lineno,
                method=call_func.attr,
                parameters=used_path_parameters,
                post_authorized=post_authorized,
            )
        )
    return calls


def _unscoped_by_id_calls() -> list[_ByIdCall]:
    return [
        call
        for key, py_file, func in _route_handlers()
        if key not in _ALLOWLIST
        for call in _by_id_service_calls(key, py_file, func, _parse(py_file))
    ]


def _post_authorized_exemptions(calls: list[_ByIdCall]) -> list[_ByIdCall]:
    """The individually authorized occurrences named by the exemption map."""
    return [call for call in calls if call.key in _POST_AUTHORIZED and call.post_authorized]


def _post_authorized_violations(
    calls: list[_ByIdCall], exemptions: list[_ByIdCall]
) -> list[_ByIdCall]:
    exempted = {id(call) for call in exemptions}
    return [call for call in calls if id(call) not in exempted]


def _post_authorized_staleness(calls: list[_ByIdCall]) -> list[str]:
    """Missing, unauthorized, and duplicate occurrences all invalidate an exemption."""
    stale: list[str] = []
    for key in sorted(_POST_AUTHORIZED):
        occurrences = [call for call in calls if call.key == key]
        if not occurrences:
            stale.append(f"{key}: no matching call")
        elif len(occurrences) != 1:
            stale.append(f"{key}: duplicate call occurrences at {[c.line for c in occurrences]}")
        elif not occurrences[0].post_authorized:
            stale.append(f"{key}: call at line {occurrences[0].line} lacks earlier authorization")
    return stale


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
        identity = _identity_param(func, _parse(py_file))
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
def test_by_id_service_calls_receive_identity() -> None:
    """Every service call consuming a path id receives identity in that same call."""
    calls = _unscoped_by_id_calls()
    violations = [
        f"{call.path}:{call.line} — {call.key} uses path parameter(s) "
        f"{sorted(call.parameters)} without identity; pass identity=identity to this call, "
        "or document its earlier scoped load in _POST_AUTHORIZED"
        for call in _post_authorized_violations(calls, _post_authorized_exemptions(calls))
        if call.key not in _BY_ID_CALL_ALLOWLIST
    ]
    assert not violations, "By-id service calls without identity:\n" + "\n".join(violations)


@pytest.mark.arch
def test_post_authorized_allowlist_is_not_stale() -> None:
    """A post-authorized call still needs an earlier identity-scoped call with the same id."""
    stale = _post_authorized_staleness(_unscoped_by_id_calls())
    assert not stale, "Remove or re-authorize stale _POST_AUTHORIZED entries:\n" + "\n".join(stale)


@pytest.mark.arch
def test_by_id_call_allowlist_is_not_stale() -> None:
    """Org-wide call exemptions identify one existing identity-less occurrence."""
    calls = _unscoped_by_id_calls()
    stale = []
    for key in _BY_ID_CALL_ALLOWLIST:
        occurrences = [call for call in calls if call.key == key]
        if len(occurrences) != 1:
            stale.append(f"{key}: expected one call, found {len(occurrences)}")
    assert not stale, "Remove stale org-wide by-id call exemptions:\n" + "\n".join(stale)


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
        if any(_is_identity_annotation(arg.annotation, _parse(py_file)) for arg, _ in _params(func))
        and _identity_param(func, _parse(py_file)) is None
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
        if _identity_dependency_without_param(
            func, _identity_gated_routers(_parse(py_file)), _parse(py_file)
        )
    ]
    assert not unbound, "Identity resolved without a parameter:\n" + "\n".join(unbound)


@pytest.mark.arch
def test_route_identity_check_finds_routes() -> None:
    """Guard against the walker silently matching nothing (e.g. a decorator rename)."""
    total = sum(
        1
        for _, py_file, func in _route_handlers()
        if _identity_param(func, _parse(py_file)) is not None
    )
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
def test_side_call_identity_does_not_scope_by_id_read() -> None:
    """Self-check: identity on an unrelated side call does not authorize a by-id read."""
    func = _parse_func(
        "@router.get('/things/{thing_id}')\n"
        "async def r(svc: FooService = Depends(get_foo_service),"
        " identity: Identity = get_current_identity()):\n"
        "    await svc.record_view(identity=identity)\n"
        "    return await svc.get(thing_id)\n"
    )
    calls = _by_id_service_calls("self/r", Path("<self-check>"), func)
    assert len(calls) == 1
    assert calls[0].method == "get"
    assert not calls[0].post_authorized


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


@pytest.mark.arch
def test_broker_api_route_and_identity_alias_are_collected() -> None:
    """Self-check: the broker proxy's decorator and dependency alias stay in scope."""
    handlers = {key: func for key, _, func in _route_handlers()}
    assert "broker/execute.proxy" in handlers
    assert _identity_param(handlers["broker/execute.proxy"]) == "identity"


@pytest.mark.arch
def test_current_identity_alias_is_resolved() -> None:
    """Self-check: an imported ``CurrentIdentity`` alias resolves to its dependency."""
    func = _parse_func(
        "@router.get('/things')\n"
        "async def r(svc: FooService, identity: CurrentIdentity):\n"
        "    return await svc.list(identity=identity)\n"
    )
    assert _identity_param(func) == "identity"


@pytest.mark.arch
def test_resource_path_keys_and_expressions_are_collected() -> None:
    """Self-check: resource keys and attribute arguments receive the same by-id guard."""
    func = _parse_func(
        "@router.post('/things/{slug}/actions/{action}')\n"
        "async def r(svc: FooService, slug: str, action: str, request: Request,"
        " identity: Identity = get_current_identity()):\n"
        "    return await svc.act(slug, request.action, request.body)\n"
    )
    calls = _by_id_service_calls("self/r", Path("<self-check>"), func)
    assert len(calls) == 1
    assert calls[0].parameters == {"slug", "action"}


@pytest.mark.arch
def test_early_post_authorized_call_cannot_hide() -> None:
    """Self-check: a later authorized call cannot retroactively exempt an earlier one."""
    func = _parse_func(
        "@router.get('/things/{credential_id}')\n"
        "async def r(svc: FooService, credential_id: str,"
        " identity: Identity = get_current_identity()):\n"
        "    await svc.scope_options(credential_id)\n"
        "    await svc.get_binding_access(credential_id, identity=identity)\n"
        "    return await svc.scope_options(credential_id)\n"
    )
    calls = _by_id_service_calls(
        "control/credentials.list_agent_permissions", Path("<self-check>"), func
    )
    exemptions = _post_authorized_exemptions(calls)
    violations = _post_authorized_violations(calls, exemptions)
    assert [call.line for call in violations] == [3]
    assert [call.post_authorized for call in calls] == [False, True]
    assert len(exemptions) == 1
    assert "duplicate call occurrences" in _post_authorized_staleness(calls)[0]


@pytest.mark.arch
def test_post_authorized_requires_documented_authorizer() -> None:
    """Self-check: only the documented get_binding_access authorizer post-authorizes.

    ``record_view`` or any other identity-bearing call cannot authorize
    ``scope_options``; only ``get_binding_access`` carrying identity and the same
    path parameter does.
    """
    wrong_authorizer = _parse_func(
        "@router.get('/things/{credential_id}')\n"
        "async def r(svc: FooService, credential_id: str,"
        " identity: Identity = get_current_identity()):\n"
        "    await svc.record_view(credential_id, identity=identity)\n"
        "    return await svc.scope_options(credential_id)\n"
    )
    wrong_calls = _by_id_service_calls(
        "control/credentials.list_agent_permissions", Path("<self-check>"), wrong_authorizer
    )
    assert len(wrong_calls) == 1
    assert not wrong_calls[0].post_authorized, "record_view must not post-authorize scope_options"

    right_authorizer = _parse_func(
        "@router.get('/things/{credential_id}')\n"
        "async def r(svc: FooService, credential_id: str,"
        " identity: Identity = get_current_identity()):\n"
        "    await svc.get_binding_access(credential_id, identity=identity)\n"
        "    return await svc.scope_options(credential_id)\n"
    )
    right_calls = _by_id_service_calls(
        "control/credentials.list_agent_permissions", Path("<self-check>"), right_authorizer
    )
    assert len(right_calls) == 1
    assert right_calls[0].post_authorized, "get_binding_access must post-authorize scope_options"


@pytest.mark.arch
def test_post_authorizer_binds_credential_to_its_documented_argument() -> None:
    """Self-check: credential_id in another argument does not post-authorize the read."""
    wrong_position = _parse_func(
        "@router.get('/things/{credential_id}')\n"
        "async def r(svc: FooService, credential_id: str,"
        " identity: Identity = get_current_identity()):\n"
        "    await svc.get_binding_access('other', credential_id, identity=identity)\n"
        "    return await svc.scope_options(credential_id)\n"
    )
    wrong_calls = _by_id_service_calls(
        "control/credentials.list_agent_permissions", Path("<self-check>"), wrong_position
    )
    by_keyword = _parse_func(
        "@router.get('/things/{credential_id}')\n"
        "async def r(svc: FooService, credential_id: str,"
        " identity: Identity = get_current_identity()):\n"
        "    await svc.get_binding_access(\n"
        "        agent_id='agent', credential_id=credential_id, identity=identity\n"
        "    )\n"
        "    return await svc.scope_options(credential_id)\n"
    )
    keyword_calls = _by_id_service_calls(
        "control/credentials.list_agent_permissions", Path("<self-check>"), by_keyword
    )
    assert len(wrong_calls) == 1
    assert len(keyword_calls) == 1
    assert [wrong_calls[0].post_authorized, keyword_calls[0].post_authorized] == [False, True]
