"""Structural route-auth gate: every served route authenticates or is declared public.

The gate builds the real FastAPI apps for every deployment shape (the combined
control-plane app and each standalone surface, broker included) through the
same composition root ``jentic-one serve`` uses, so routes mounted from
``shared/web/`` (agent discovery, ``/instance``, ``/reference/endpoints.json``,
the SPA shell, the per-surface health probes), routers created inside factory
functions, and container-installed routes are all covered. Each route is
classified from its resolved dependency tree — never from source text — so a
comment, docstring, or unused import that mentions ``Identity`` cannot satisfy
it (#1375).

A route passes when one of these holds:

- its dependency tree contains the ``get_current_identity`` closure, or one of
  the broker's identity dependencies (:data:`_BROKER_AUTH_DEPENDENCIES`);
- it is listed in ``NON_IDENTITY_AUTH`` (authenticates with a non-platform
  credential, e.g. an RFC 7592 Registration-Access-Token);
- its operationId is in ``PUBLIC_OPERATION_IDS``
  (``src/jentic_one/shared/web/openapi_meta/operation_ids.py``), each entry carrying a ``#``
  justification comment directly above it;
- it is schema-hidden and listed in :data:`SCHEMA_HIDDEN_PUBLIC_OPERATIONS`
  with a justification, or it is a broker liveness/readiness probe
  (``_BROKER_PUBLIC_PATHS``).

Non-``APIRoute`` routes (raw Starlette ``Route``/``Mount``) carry no FastAPI
dependency tree, so each one must be listed in :data:`OPAQUE_ROUTES`.

Every declaration is also checked for staleness: an entry that matches no real
route on any built app fails the gate.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute, _IncludedRouter

from jentic_one.composition import build_app, build_context
from jentic_one.mcp.app import McpChallengePlaceholder, McpMount
from jentic_one.shared.config import load_config
from jentic_one.shared.web import static as static_module
from jentic_one.shared.web.deps import get_current_identity
from jentic_one.shared.web.endpoint_permissions import (
    NON_IDENTITY_AUTH,
    _normalise_path,
)
from jentic_one.shared.web.endpoint_reference import _BROKER_PUBLIC_PATHS
from jentic_one.shared.web.openapi_meta import PUBLIC_OPERATION_IDS
from jentic_one.testing.introspect import RouteFact, route_facts

from .conftest import SRC_ROOT

REPO_ROOT = SRC_ROOT.parent.parent
OPENAPI_META = SRC_ROOT / "shared" / "web" / "openapi_meta" / "operation_ids.py"
GATE_CONFIG = REPO_ROOT / "config" / "local-sqlite.yaml"

#: Every deployment shape ``jentic-one serve`` can build: the combined
#: control-plane app plus each surface standalone (the broker only ever runs
#: standalone).
APP_SHAPES: dict[str, list[str]] = {
    "combined": ["control", "admin", "auth", "registry"],
    "control": ["control"],
    "admin": ["admin"],
    "auth": ["auth"],
    "registry": ["registry"],
    "broker": ["broker"],
}

#: Schema-hidden (``include_in_schema=False``) public routes that are not in
#: ``PUBLIC_OPERATION_IDS``. They never appear in an OpenAPI document, so the
#: spec-shaping set does not need them; the justification lives here instead.
#: operationId -> justification. Every entry must be schema-hidden on every app
#: that serves it and must match a real route.
SCHEMA_HIDDEN_PUBLIC_OPERATIONS: dict[str, str] = {
    "skillsIndex": (
        "Agent-discovery skill index (shared/web/agent_discovery.py): static, "
        "packaged skill metadata an agent reads before it holds any credential."
    ),
    "onboardingSkillAlias": (
        "GET /SKILL.md alias of the onboarding skill: static packaged markdown "
        "an agent reads before it holds any credential."
    ),
    "skillMarkdown": (
        "Packaged skill markdown (GET /skills/{name}.md): static documents with "
        "no tenant data, served pre-auth by design for agent discovery."
    ),
    "skillReference": (
        "Packaged skill reference files: static documents with no tenant data, "
        "served pre-auth by design for agent discovery."
    ),
    "llmsTxt": (
        "llms.txt (root and /.well-known): static discovery pointer for LLM "
        "agents; carries no tenant data."
    ),
    "endpointsReference": (
        "GET /reference/endpoints.json: the endpoint/permission reference, the "
        "same payload as the committed docs/reference/endpoints.json; route and "
        "permission metadata only, no tenant data."
    ),
    "appConfig": (
        "SPA boot config (GET /app-config.json): only the health-probe path the "
        "SPA must call before sign-in."
    ),
    "rootRedirect": "Bare-root 307 redirect into the SPA under /app; serves no data.",
    "iconRedirect": (
        "Browser/OS favicon and apple-touch-icon probes at the site root; 307 "
        "redirects to the static SPA asset."
    ),
}

#: Raw Starlette routes (no FastAPI dependency tree to introspect).
#: path -> (justification, allowed endpoint types or ``None`` for any).
OPAQUE_ROUTES: dict[str, tuple[str, tuple[type, ...] | None]] = {
    "/openapi.json": ("FastAPI's generated OpenAPI document (public API description).", None),
    "/docs": ("FastAPI's Swagger UI shell over the public OpenAPI document.", None),
    "/docs/oauth2-redirect": ("Swagger UI OAuth2 redirect helper page.", None),
    "/redoc": ("FastAPI's ReDoc shell over the public OpenAPI document.", None),
    "/mcp": (
        "MCP mount (or its discovery-challenge placeholder): authenticates inside "
        "the ASGI app via the app's verify_token and answers 401 + "
        "WWW-Authenticate otherwise.",
        (McpMount, McpChallengePlaceholder),
    ),
    "/metrics": (
        "Prometheus scrape endpoint, mounted only when the operator enables the "
        "Prometheus reader; exposure is an operator deployment decision.",
        None,
    ),
}

#: Opaque routes whose presence depends on process-global configuration, so
#: they are exempt from the staleness check.
_CONDITIONAL_OPAQUE_ROUTES: frozenset[str] = frozenset({"/metrics"})

#: Minimum justification length: enough to rule out ``# ok`` / ``# public``.
_MIN_JUSTIFICATION_WORDS = 4


@dataclass(frozen=True)
class RouteRecord:
    """One (method, route) pair on one built app, classified from its dependencies."""

    shape: str
    method: str
    path: str
    operation_id: str | None
    in_schema: bool
    auth_via: str | None
    location: str
    opaque: bool = False
    endpoint: Any = None

    @property
    def label(self) -> str:
        return f"{self.method} {self.path} (operationId={self.operation_id}, app={self.shape})"


def _location(endpoint: Any) -> str:
    """``path:line (name)`` of a route handler, relative to the repo root."""
    target = inspect.unwrap(endpoint) if callable(endpoint) else endpoint
    if not (inspect.isfunction(target) or inspect.ismethod(target)):
        target = type(target)
    try:
        source = Path(inspect.getsourcefile(target) or "?").resolve()
        line = inspect.getsourcelines(target)[1]
    except (OSError, TypeError):
        return f"<unknown source> ({getattr(target, '__qualname__', target)!r})"
    try:
        shown = source.relative_to(REPO_ROOT)
    except ValueError:
        shown = source
    return f"{shown}:{line} ({getattr(target, '__qualname__', '?')})"


def _walk(routes: list[Any], prefix: str = "") -> Iterator[tuple[str, Any]]:
    """Flatten an app's routes, threading include-level prefix."""
    for route in routes:
        if isinstance(route, _IncludedRouter):
            context = route.include_context
            yield from _walk(
                route.original_router.routes,
                prefix + context.prefix,
            )
        else:
            yield prefix, route


def _route_endpoints_by_key(app: FastAPI) -> dict[tuple[str, str], tuple[str, Any]]:
    """Map ``(method, path)`` to ``(location, endpoint)`` for diagnostic formatting."""
    by_key: dict[tuple[str, str], tuple[str, Any]] = {}
    for prefix, route in _walk(app.routes):
        path = _normalise_path(prefix + getattr(route, "path", ""))
        if not isinstance(route, APIRoute):
            endpoint = getattr(route, "endpoint", None) or getattr(route, "app", None)
            loc = _location(endpoint) if endpoint is not None else type(route).__name__
            by_key.setdefault(("*", path), (loc, endpoint))
            continue
        loc = _location(route.endpoint)
        for method in sorted((route.methods or set()) - {"HEAD", "OPTIONS"}):
            by_key.setdefault((method, path), (loc, route.endpoint))
    return by_key


def _record_from_fact(
    fact: RouteFact,
    shape: str,
    by_key: dict[tuple[str, str], tuple[str, Any]],
) -> RouteRecord:
    loc, endpoint = by_key.get((fact.method, fact.path), (fact.module, None))
    return RouteRecord(
        shape=shape,
        method=fact.method,
        path=fact.path,
        operation_id=fact.operation_id,
        in_schema=fact.in_schema,
        auth_via=fact.auth_via,
        location=loc,
        opaque=fact.opaque,
        endpoint=endpoint if fact.opaque else None,
    )


def collect_route_records(app: FastAPI, shape: str) -> list[RouteRecord]:
    """Classify every route the app serves (one record per HTTP method)."""
    by_key = _route_endpoints_by_key(app)
    return [_record_from_fact(fact, shape, by_key) for fact in route_facts(app)]


@pytest.fixture(scope="module")
def route_records(tmp_path_factory: pytest.TempPathFactory) -> list[RouteRecord]:
    """Classified routes of every deployment shape, SPA shell included.

    A throwaway bundle directory stands in for ``ui/dist`` so the SPA shell
    routes are always mounted (and gated), whether or not the UI was built.
    """
    bundle = tmp_path_factory.mktemp("spa-bundle")
    (bundle / "index.html").write_text("<!doctype html>", encoding="utf-8")
    config = load_config(GATE_CONFIG)
    records: list[RouteRecord] = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(static_module, "_resolve_static_dir", lambda: bundle)
        for shape, apps in APP_SHAPES.items():
            app = build_app(build_context(config, apps), apps)
            records.extend(collect_route_records(app, shape))
    return records


_FIX_AUTH = (
    "Fix: add `identity: Identity = get_current_identity(required_permissions=[...])` "
    "to the handler. If the route is intentionally public, add its operationId to "
    "PUBLIC_OPERATION_IDS in src/jentic_one/shared/web/openapi_meta/operation_ids.py with a "
    "`#` comment directly above the entry saying why it is safe unauthenticated (or, "
    "for a schema-hidden route, to SCHEMA_HIDDEN_PUBLIC_OPERATIONS in this file)."
)


def _is_declared_public(record: RouteRecord) -> bool:
    if record.operation_id in PUBLIC_OPERATION_IDS:
        return True
    if not record.in_schema and record.operation_id in SCHEMA_HIDDEN_PUBLIC_OPERATIONS:
        return True
    return record.shape == "broker" and record.path in _BROKER_PUBLIC_PATHS


def find_unauthenticated_undeclared(records: list[RouteRecord]) -> list[str]:
    """Violations: routes with no auth dependency that no public declaration covers."""
    violations: dict[tuple[str, str, str | None], tuple[str, list[str]]] = {}
    for record in records:
        if record.opaque:
            continue
        if record.auth_via is None and not _is_declared_public(record):
            key = (record.method, record.path, record.operation_id)
            violations.setdefault(key, (record.location, []))[1].append(record.shape)
    return [
        f"{location} — {method} {path} (operationId={op_id}, apps={','.join(shapes)}) "
        "has no authentication dependency and is not declared public"
        for (method, path, op_id), (location, shapes) in sorted(violations.items())
    ]


@pytest.mark.arch
def test_every_route_authenticates_or_is_declared_public(route_records: list[RouteRecord]) -> None:
    """Every APIRoute on every app shape carries an auth dependency or a public declaration."""
    # Introspection that silently broke would read everything as public; the gate
    # then fails closed, but pin a floor so the failure names the real cause.
    authed = [r for r in route_records if r.auth_via is not None]
    assert len(authed) >= 100, (
        f"only {len(authed)} authenticated routes recovered — dependency introspection "
        "likely broke (check _IncludedRouter / get_current_identity recognition "
        "against the installed FastAPI version)"
    )
    violations = find_unauthenticated_undeclared(route_records)
    assert not violations, (
        "Routes missing authentication:\n" + "\n".join(violations) + ("\n\n" + _FIX_AUTH)
    )


@pytest.mark.arch
def test_opaque_routes_are_declared(route_records: list[RouteRecord]) -> None:
    """Raw Starlette routes have no dependency tree, so each must be declared explicitly."""
    violations: list[str] = []
    for record in route_records:
        if not record.opaque:
            continue
        declared = OPAQUE_ROUTES.get(record.path)
        if declared is None:
            violations.append(
                f"{record.location} — non-FastAPI route {record.path} (app={record.shape}) "
                "bypasses dependency-based auth and is not in OPAQUE_ROUTES"
            )
            continue
        allowed_types = declared[1]
        if allowed_types is not None and not isinstance(record.endpoint, allowed_types):
            violations.append(
                f"{record.location} — {record.path} (app={record.shape}) is served by "
                f"{type(record.endpoint).__name__}, expected one of "
                f"{[t.__name__ for t in allowed_types]}"
            )
    assert not violations, (
        "Undeclared opaque routes:\n"
        + "\n".join(violations)
        + (
            "\n\nFix: serve the route as a FastAPI path operation with an auth dependency, "
            "or (if it authenticates inside its ASGI app or is framework-public) add it to "
            "OPAQUE_ROUTES in tests/arch/test_route_auth_gate.py with a justification."
        )
    )


def public_operation_id_entries(source: str) -> list[tuple[str, int]]:
    """``(operationId, lineno)`` for each string literal in ``PUBLIC_OPERATION_IDS``."""
    tree = ast.parse(source)
    for node in tree.body:
        target: ast.expr
        value: ast.expr | None
        if isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        else:
            continue
        if isinstance(target, ast.Name) and target.id == "PUBLIC_OPERATION_IDS":
            assert value is not None
            return [
                (elt.value, elt.lineno)
                for elt in ast.walk(value)
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            ]
    raise AssertionError(
        "PUBLIC_OPERATION_IDS assignment not found in openapi_meta/operation_ids.py"
    )


def find_unjustified_public_entries(source: str, filename: str) -> list[str]:
    """Entries of ``PUBLIC_OPERATION_IDS`` without their own ``#`` comment directly above.

    The comment block must sit on the line(s) immediately above the entry, with
    no other entry between them, so a new id appended under an existing group's
    comment does not inherit that group's justification.
    """
    lines = source.splitlines()
    violations: list[str] = []
    for op_id, lineno in public_operation_id_entries(source):
        comment: list[str] = []
        index = lineno - 2  # 0-based index of the line directly above the entry
        while index >= 0 and lines[index].strip().startswith("#"):
            comment.insert(0, lines[index].strip().lstrip("#").strip())
            index -= 1
        words = " ".join(comment).split()
        if len(words) < _MIN_JUSTIFICATION_WORDS:
            violations.append(
                f"{filename}:{lineno} — PUBLIC_OPERATION_IDS entry {op_id!r} has no "
                "justification comment directly above it"
            )
    return violations


@pytest.mark.arch
def test_public_operation_ids_are_justified() -> None:
    """Each PUBLIC_OPERATION_IDS entry carries its own justification comment."""
    source = OPENAPI_META.read_text(encoding="utf-8")
    entries = public_operation_id_entries(source)
    assert {op_id for op_id, _ in entries} == set(PUBLIC_OPERATION_IDS), (
        "PUBLIC_OPERATION_IDS must be a literal set of string entries so each one "
        "can carry its own justification comment"
    )
    violations = find_unjustified_public_entries(source, str(OPENAPI_META.relative_to(REPO_ROOT)))
    assert not violations, (
        "Undocumented public operations:\n"
        + "\n".join(violations)
        + (
            "\n\nFix: put a `#` comment on the line(s) directly above the entry stating why "
            "the operation is safe to serve unauthenticated (what it returns, what binds "
            "or limits it). Repeat the rationale per entry rather than sharing one comment."
        )
    )


@pytest.mark.arch
def test_public_declarations_are_not_stale(route_records: list[RouteRecord]) -> None:
    """Every public/auth declaration names a real route on at least one app shape."""
    route_ids = {r.operation_id for r in route_records if not r.opaque}
    keys = {(r.method, r.path) for r in route_records if not r.opaque}
    opaque_paths = {r.path for r in route_records if r.opaque}
    broker_paths = {r.path for r in route_records if r.shape == "broker"}
    stale = [
        *(f"PUBLIC_OPERATION_IDS: {i!r}" for i in sorted(PUBLIC_OPERATION_IDS - route_ids)),
        *(
            f"SCHEMA_HIDDEN_PUBLIC_OPERATIONS: {i!r}"
            for i in sorted(SCHEMA_HIDDEN_PUBLIC_OPERATIONS.keys() - route_ids)
        ),
        *(f"NON_IDENTITY_AUTH: {k!r}" for k in sorted(NON_IDENTITY_AUTH.keys() - keys)),
        *(
            f"OPAQUE_ROUTES: {p!r}"
            for p in sorted(OPAQUE_ROUTES.keys() - opaque_paths - _CONDITIONAL_OPAQUE_ROUTES)
        ),
        *(f"_BROKER_PUBLIC_PATHS: {p!r}" for p in sorted(_BROKER_PUBLIC_PATHS - broker_paths)),
    ]
    assert not stale, (
        "Stale public/auth declarations (no matching route):\n"
        + "\n".join(stale)
        + (
            "\n\nFix: remove the entry, or correct it to the route's real operationId/path "
            "(a renamed handler changes its generated operationId)."
        )
    )


@pytest.mark.arch
def test_public_declarations_do_not_shadow_auth(route_records: list[RouteRecord]) -> None:
    """A route declared public must not also authenticate (and vice versa).

    Declaring an authenticated route public strips its ``security`` and 401/403
    responses from the spec, misdocumenting it; a schema-hidden allowance on a
    schema-visible route hides it from the PUBLIC_OPERATION_IDS review surface.
    """
    violations: list[str] = []
    for record in route_records:
        if record.opaque:
            continue
        public = record.operation_id in PUBLIC_OPERATION_IDS
        if public and record.auth_via is not None:
            violations.append(
                f"{record.location} — {record.label} is in PUBLIC_OPERATION_IDS but "
                f"authenticates via {record.auth_via}; remove it from PUBLIC_OPERATION_IDS"
            )
        if record.in_schema and record.operation_id in SCHEMA_HIDDEN_PUBLIC_OPERATIONS:
            violations.append(
                f"{record.location} — {record.label} is schema-visible but listed in "
                "SCHEMA_HIDDEN_PUBLIC_OPERATIONS; move it to PUBLIC_OPERATION_IDS"
            )
    assert not violations, "Contradictory public declarations:\n" + "\n".join(violations)


# --- self-tests: the gate cannot be satisfied by source text -----------------


@pytest.mark.arch
def test_gate_ignores_identity_mentions_in_source() -> None:
    """A route whose module only *mentions* Identity is still flagged (#1375).

    The substring scan this gate replaces accepted any file containing the word
    ``Identity``; the structural gate looks only at the dependency tree.
    """
    router = APIRouter()

    # NOTE: Identity / get_current_identity / required_permissions — text only.
    @router.get("/leaky", operation_id="leakyGateProbe")
    async def _leaky() -> dict[str, str]:
        """Mentions Identity and get_current_identity, depends on neither."""
        return {}

    @router.get("/guarded", operation_id="guardedGateProbe")
    async def _guarded(
        _identity: object = get_current_identity(required_permissions=["agents:read"]),
    ) -> dict[str, str]:
        return {}

    app = FastAPI()
    app.include_router(router, prefix="/probe")
    violations = find_unauthenticated_undeclared(collect_route_records(app, "probe"))
    assert len(violations) == 1
    assert "GET /probe/leaky (operationId=leakyGateProbe" in violations[0]
    assert "test_route_auth_gate.py" in violations[0]


@pytest.mark.arch
def test_gate_sees_include_level_auth_dependency() -> None:
    """Auth applied via ``include_router(dependencies=...)`` counts as authentication."""
    router = APIRouter()

    @router.get("/inner", operation_id="innerGateProbe")
    async def _inner() -> dict[str, str]:
        return {}

    app = FastAPI()
    app.include_router(router, dependencies=[get_current_identity()])
    records = collect_route_records(app, "probe")
    assert [r.auth_via for r in records if r.path == "/inner"] == ["get_current_identity"]


@pytest.mark.arch
def test_justification_check_rejects_shared_comment() -> None:
    """An entry appended under another entry's comment is unjustified."""
    source = (
        "PUBLIC_OPERATION_IDS: frozenset[str] = frozenset(\n"
        "    {\n"
        "        # Liveness probe: dependency-free, returns only a status string.\n"
        '        "getHealth",\n'
        '        "sneakyOp",\n'
        "        # ok\n"
        '        "tersOp",\n'
        "    }\n"
        ")\n"
    )
    violations = find_unjustified_public_entries(source, "openapi_meta.py")
    assert [v.split(" — ")[0] for v in violations] == ["openapi_meta.py:5", "openapi_meta.py:7"]
