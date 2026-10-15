"""Architecture enforcement: scoped routes hand the caller's identity to their service.

``test_scoped_list_reads.py`` proves service list reads take and use
``identity``; this proves the routes actually pass it. A service method that
takes no identity at all passes every service-side check trivially, so the
route is where an unscoped by-id read or write shows up.

Every GET, PUT, PATCH and DELETE handler, plus every POST on a path with a
``{param}`` (an action on one resource), in the ``admin``, ``auth`` and
``control`` routers must pass the identity it gets from
``get_current_identity`` into at least one call. A route that is deliberately
not caller-scoped goes in ``_ALLOWLIST`` with the reason it is safe, and an
entry that no longer matches an unscoped route fails the test.

Routes without a ``get_current_identity`` dependency (health, discovery,
OAuth browser pages) are out of scope here; ``test_web_layer.py`` decides
which routes may be unauthenticated.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from .conftest import SRC_ROOT, python_files_in

SCOPED_WEB_DIRS = [
    SRC_ROOT / "admin" / "web",
    SRC_ROOT / "auth" / "web",
    SRC_ROOT / "control" / "web",
]

_ROUTE_VERBS = {"get", "put", "patch", "delete", "post"}

#: ``<surface>/<module>.<handler>`` -> why it is safe not to pass identity on.
_ALLOWLIST: dict[str, str] = {
    "admin/auth.get_current_user": "self-lookup keyed on identity.sub (GET /users/me)",
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
    "control/credentials.list_providers": "static credential-provider metadata, no rows",
    "control/oauth_app_registrations.list_oauth_app_registrations": (
        "org-wide OAuth app registrations, no owner column; requires org:admin"
    ),
    "control/oauth_app_registrations.get_oauth_app_registration": (
        "org-wide OAuth app registrations, no owner column; requires org:admin"
    ),
    "control/vendors.list_vendors": "public vendor registry, no per-caller rows",
    "control/vendors.get_auth_capabilities": "public vendor auth metadata, no per-caller rows",
    "auth/registration.update_registration_endpoint": "always 403: client update unsupported",
    "auth/registration.delete_registration_endpoint": "always 403: client deletion unsupported",
}


def _route_path(func: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, str | None] | None:
    """``(verb, path)`` for a route handler, or ``None`` if it is not one."""
    for dec in func.decorator_list:
        if (
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and dec.func.attr in _ROUTE_VERBS
        ):
            path = dec.args[0].value if dec.args and isinstance(dec.args[0], ast.Constant) else None
            return dec.func.attr, path if isinstance(path, str) else None
    return None


def _identity_param(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """Name of the parameter defaulting to ``get_current_identity(...)``, if any."""
    positional = func.args.posonlyargs + func.args.args
    pos_defaults: list[ast.expr | None] = [None] * (
        len(positional) - len(func.args.defaults)
    ) + list(func.args.defaults)
    pairs = list(zip(positional, pos_defaults, strict=True)) + list(
        zip(func.args.kwonlyargs, func.args.kw_defaults, strict=True)
    )
    for arg, default in pairs:
        if (
            isinstance(default, ast.Call)
            and isinstance(default.func, ast.Name)
            and default.func.id == "get_current_identity"
        ):
            return arg.arg
    return None


def _passes(func: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    """Whether *name* is passed (positionally or by keyword) to any call in *func*."""
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        values = list(node.args) + [kw.value for kw in node.keywords]
        if any(isinstance(v, ast.Name) and v.id == name for v in values):
            return True
    return False


def _in_scope(verb: str, path: str | None) -> bool:
    if verb != "post":
        return True
    # A POST on a collection creates; a POST on ``/{id}…`` acts on one resource.
    return path is None or "{" in path


def _unscoped_routes() -> dict[str, tuple[Path, int]]:
    """``<surface>/<module>.<handler>`` -> (file, line) for every route not passing identity."""
    found: dict[str, tuple[Path, int]] = {}
    for web_dir in SCOPED_WEB_DIRS:
        surface = web_dir.parent.name
        for py_file in python_files_in(web_dir):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                route = _route_path(node)
                if route is None or not _in_scope(*route):
                    continue
                identity = _identity_param(node)
                if identity is None or _passes(node, identity):
                    continue
                found[f"{surface}/{py_file.stem}.{node.name}"] = (py_file, node.lineno)
    return found


@pytest.mark.arch
def test_scoped_routes_pass_identity_to_their_service() -> None:
    """Every scoped read and by-id route passes identity on, or is allowlisted with a reason."""
    violations = [
        f"{path}:{line} — {name} takes the caller's identity but never passes it to its "
        "service; pass `identity=identity` and scope the query with it (or add the route "
        "to _ALLOWLIST with a reason)"
        for name, (path, line) in sorted(_unscoped_routes().items())
        if name not in _ALLOWLIST
    ]
    assert not violations, "Routes not passing identity:\n" + "\n".join(violations)


@pytest.mark.arch
def test_route_identity_allowlist_is_not_stale() -> None:
    """Allowlist entries must name a route that still does not pass identity."""
    unscoped = _unscoped_routes()
    stale = sorted(name for name in _ALLOWLIST if name not in unscoped)
    assert not stale, (
        "Remove stale _ALLOWLIST entries (route gone or now passes identity):\n" + "\n".join(stale)
    )


@pytest.mark.arch
def test_route_identity_check_finds_routes() -> None:
    """Guard against the walker silently matching nothing (e.g. a decorator rename)."""
    total = 0
    for web_dir in SCOPED_WEB_DIRS:
        for py_file in python_files_in(web_dir):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            total += sum(
                1
                for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and _route_path(node) is not None
                and _identity_param(node) is not None
            )
    assert total > 50, f"Only {total} authenticated routes found; the route walker is broken"
