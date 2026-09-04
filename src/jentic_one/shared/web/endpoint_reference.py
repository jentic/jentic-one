"""Canonical endpoint → scope → typical-caller reference, built from code.

This is the single source of truth for the *authorization* reference — which
actor types may call each operation, the scope(s) it requires, the typical
caller, and any non-standard credential note. It is deliberately **separate**
from the OpenAPI document:

- The OpenAPI spec models only the real *authentication* mechanism
  (``BearerAuth`` — an opaque bearer token). It does **not** express per-operation
  scopes/actor types, because OpenAPI's ``security`` model cannot faithfully carry
  our authorization semantics (OR-of-scopes, the ``org:admin`` superuser bypass,
  the typical-caller hint, and scopes enforced in the service layer rather than at
  the gateway). Encoding those as a fabricated OAuth2 flow would misrepresent
  enforcement.
- This module is therefore the authoritative, machine-readable authorization
  reference that the CLI and docs SPA consume.

Consumers:

- ``tools/endpoint_tree.py`` renders this to ``docs/reference/endpoints.{md,json}``.
  It introspects a freshly-built broker app for the broker surface.
- ``GET /reference/endpoints.json`` serves :func:`build_reference_payload` live.
  It is mounted only on the control-plane app (the broker runs as a separate
  process), so it cannot introspect the broker; the broker surface is supplied
  declaratively by :func:`_declared_broker_endpoints` instead.

Both paths therefore call :func:`build_reference_payload` over the same set of
endpoints and serve byte-identical payloads. A drift test
(:mod:`tests.arch.test_endpoint_tree`) pins the declared broker surface to the
real broker app and asserts the live payload equals the committed file.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from jentic_one.shared.scopes import BROKER_EXECUTE_SCOPE
from jentic_one.shared.web.endpoint_scopes import build_operation_auth_map, implied_scopes
from jentic_one.shared.web.openapi_meta import PUBLIC_OPERATION_IDS
from jentic_one.shared.web.scope_catalog import build_scope_catalog

if TYPE_CHECKING:
    from fastapi import FastAPI

#: Schema identifier for the JSON payload (bump on a breaking shape change).
REFERENCE_SCHEMA = "jentic.endpoint-scope-tree/v1"

_HTTP_METHODS = ("get", "post", "put", "patch", "delete")

#: Actor types that ride the agent token flow (mirrored in the Go CLI grouping).
_AGENT_ACTORS: tuple[str, ...] = ("agent", "service_account", "toolkit")

#: The broker's only unauthenticated routes (liveness / readiness probes); every
#: other broker route is the execute proxy and requires BROKER_EXECUTE_SCOPE.
_BROKER_PUBLIC_PATHS: frozenset[str] = frozenset({"/health", "/ready"})

#: The broker's authenticated data-plane route — the catch-all execute proxy. Its
#: hand-curated spec carries no scope metadata, so it is annotated explicitly
#: rather than by "everything that isn't a probe", so an unexpected new broker
#: route is NOT silently mislabelled as the proxy (it falls through to the guard).
_BROKER_PROXY_PATH = "/{upstream_url}"

#: HTTP methods the broker execute-proxy accepts on :data:`_BROKER_PROXY_PATH`.
_BROKER_PROXY_METHODS: tuple[str, ...] = ("DELETE", "GET", "PATCH", "POST", "PUT")

# Top-level grouping by the *typical caller* hint (advisory; the scope is the
# real gate). Endpoints are scope-gated, not actor-gated, so we group by who
# usually calls a route rather than by an enforced actor restriction.
GROUP_PUBLIC = "Public (unauthenticated)"
GROUP_AGENT = "Agent-facing (typically agent / service-account / toolkit)"
GROUP_OPERATOR = "Operator-facing (typically a human operator / admin)"
GROUP_ANY = "Any authenticated actor"

#: typical_caller value -> display group.
_GROUP_BY_TYPICAL = {
    "agent": GROUP_AGENT,
    "operator": GROUP_OPERATOR,
    "any": GROUP_ANY,
}

#: Stable group display order (used by renderers and consumers).
GROUP_ORDER = (GROUP_AGENT, GROUP_OPERATOR, GROUP_ANY, GROUP_PUBLIC)


@dataclass
class Endpoint:
    """A single (method, path) endpoint with its recovered auth metadata."""

    method: str
    path: str
    surface: str
    summary: str
    operation_id: str | None
    authenticated: bool
    public: bool
    actor_types: list[str] = field(default_factory=list)
    required_scopes: list[str] = field(default_factory=list)
    implied_scopes: dict[str, list[str]] = field(default_factory=dict)
    auth_note: str | None = None
    #: Advisory hint (``agent`` / ``operator`` / ``any``) at who usually calls the
    #: endpoint. NOT an enforced restriction — the scope is the real gate.
    typical_caller: str | None = None

    @property
    def group(self) -> str:
        if self.public or not self.authenticated:
            return GROUP_PUBLIC
        return _GROUP_BY_TYPICAL.get(self.typical_caller or "any", GROUP_ANY)


def _surface_for(path: str, default: str) -> str:
    """Best-effort surface label from the path prefix (display only)."""
    first = path.strip("/").split("/", 1)[0]
    if first.startswith("{"):
        # Templated first segment (e.g. the broker catch-all /{upstream_url}).
        return default
    return first or default


def _endpoints_from_app(app: FastAPI, surface_default: str) -> list[Endpoint]:
    """Build endpoints for one app from its routes joined with the auth map.

    The scope/actor/typical_caller/auth_note all come from
    :func:`build_operation_auth_map` (the curated source of truth) — never from
    OpenAPI vendor extensions (the spec carries none).
    """
    auth_map = build_operation_auth_map(app)
    spec: dict[str, Any] = app.openapi()
    endpoints: list[Endpoint] = []
    for path, item in spec.get("paths", {}).items():
        if not isinstance(item, dict):
            continue
        for method, operation in item.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            info = auth_map.get((method.upper(), path))
            if info is not None:
                authenticated = bool(info["authenticated"])
                scopes = list(info["scopes"])
                actor_types = list(info["actor_types"])
                typical_caller = info.get("typical_caller")
                auth_note = info.get("auth_note")
            else:
                # Route not in the control auth map (e.g. broker, whose hand-curated
                # app is annotated separately below). Fall back to the spec's native
                # security: [] == public.
                authenticated = operation.get("security") != []
                scopes = []
                actor_types = []
                typical_caller = None
                auth_note = None
            endpoints.append(
                Endpoint(
                    method=method.upper(),
                    path=path,
                    surface=_surface_for(path, surface_default),
                    summary=operation.get("summary", ""),
                    operation_id=operation.get("operationId"),
                    authenticated=authenticated,
                    public=not authenticated,
                    actor_types=actor_types,
                    required_scopes=scopes,
                    implied_scopes=implied_scopes(scopes) if scopes else {},
                    auth_note=auth_note,
                    typical_caller=typical_caller,
                )
            )
    return endpoints


def _declared_broker_endpoints() -> list[Endpoint]:
    """The broker's full HTTP surface, declared (not introspected).

    The broker runs as a separate process, so the control-plane app that serves
    ``GET /reference/endpoints.json`` cannot introspect it. Its surface is tiny and
    static — two liveness/readiness probes plus the catch-all execute proxy — so we
    declare it here and contribute it whenever a live broker app is not supplied.

    :func:`tests.arch.test_endpoint_tree` builds the real broker app and asserts
    this declaration matches it exactly, so it cannot silently drift from code.
    """
    probes = [
        Endpoint(
            method="GET",
            path="/health",
            surface="health",
            summary="Broker health",
            operation_id="brokerHealth",
            authenticated=False,
            public=True,
        ),
        Endpoint(
            method="GET",
            path="/ready",
            surface="ready",
            summary="Broker readiness (saturation-aware)",
            operation_id="brokerReady",
            authenticated=False,
            public=True,
        ),
    ]
    proxy = [
        Endpoint(
            method=method,
            path=_BROKER_PROXY_PATH,
            surface="broker",
            summary="Execute an upstream API operation",
            operation_id="proxy",
            authenticated=True,
            public=False,
            actor_types=list(_AGENT_ACTORS),
            required_scopes=[BROKER_EXECUTE_SCOPE],
            implied_scopes=implied_scopes([BROKER_EXECUTE_SCOPE]),
            typical_caller="agent",
        )
        for method in _BROKER_PROXY_METHODS
    ]
    return probes + proxy


def _annotate_broker(broker: list[Endpoint]) -> None:
    """Stamp the broker execute-proxy route's enforced scope (its spec lacks the auth map).

    The broker proxy (:data:`_BROKER_PROXY_PATH`) enforces BROKER_EXECUTE_SCOPE via
    RequireToolkitAccess (broker/web/deps.require_execute_scope); its hand-curated
    spec does not carry scope metadata, so annotate that specific data-plane route
    to keep the reference code-true. The liveness/readiness probes are the broker's
    only public routes.

    Only the *known* proxy path is annotated — not "any non-probe route" — so a new
    or unexpected broker route is left as-is and reaches
    :func:`assert_broker_classification_is_sound`, which fails loud rather than
    silently labelling it as the execute proxy.
    """
    for ep in broker:
        if ep.path == _BROKER_PROXY_PATH and not ep.required_scopes:
            ep.authenticated = True
            ep.public = False
            ep.required_scopes = [BROKER_EXECUTE_SCOPE]
            ep.implied_scopes = implied_scopes([BROKER_EXECUTE_SCOPE])
            ep.actor_types = ep.actor_types or list(_AGENT_ACTORS)
            ep.typical_caller = ep.typical_caller or "agent"


def _dedupe(endpoints: list[Endpoint]) -> list[Endpoint]:
    seen: dict[tuple[str, str], Endpoint] = {}
    for ep in endpoints:
        seen.setdefault((ep.method, ep.path), ep)
    return sorted(seen.values(), key=lambda e: (e.path, e.method))


def assert_classification_is_sound(
    control: list[Endpoint], public_operation_ids: frozenset[str]
) -> None:
    """Fail loudly if the public/authenticated boundary looks broken.

    The classification depends on introspecting private FastAPI internals
    (``endpoint_scopes._closure_values`` / ``_IncludedRouter``). If a future
    FastAPI refactor breaks that, every route would silently fall back to
    "public", quietly emptying the reference. These invariants turn that silent
    failure into a loud error:

    1. A non-trivial number of control-plane operations must authenticate
       (introspection produced *something*).
    2. Every control-plane operation rendered as public must be an explicitly
       declared public operation (``PUBLIC_OPERATION_IDS``) — never a route that
       merely failed introspection.
    """
    authed = [ep for ep in control if ep.authenticated]
    if len(authed) < 10:
        raise RuntimeError(
            "endpoint-reference: only "
            f"{len(authed)} authenticated control-plane operations recovered — "
            "scope introspection likely broke (check endpoint_scopes._closure_values "
            "against the current FastAPI version)."
        )
    leaked = [
        f"{ep.method} {ep.path} (operationId={ep.operation_id})"
        for ep in control
        if ep.public and ep.operation_id not in public_operation_ids
    ]
    if leaked:
        raise RuntimeError(
            "endpoint-reference: operation(s) classified public without being declared "
            "in PUBLIC_OPERATION_IDS — introspection may have failed open:\n  "
            + "\n  ".join(sorted(leaked))
        )


def assert_broker_classification_is_sound(broker: list[Endpoint]) -> None:
    """Fail loudly if a broker route is rendered public outside the known probes.

    The broker has no entry in the control auth map, so :func:`_endpoints_from_app`
    classifies its routes from the spec's native ``security`` and
    :func:`_annotate_broker` stamps the execute proxy's scope. The only routes that
    may legitimately be public are the liveness/readiness probes
    (:data:`_BROKER_PUBLIC_PATHS`); anything else rendered public means the broker
    spec lost its ``security`` (or grew an unguarded route) and would silently
    advertise a protected proxy route as open — turn that into a loud error.
    """
    leaked = [
        f"{ep.method} {ep.path} (operationId={ep.operation_id})"
        for ep in broker
        if ep.public and ep.path not in _BROKER_PUBLIC_PATHS
    ]
    if leaked:
        raise RuntimeError(
            "endpoint-reference: broker operation(s) classified public outside the "
            "known liveness/readiness probes — the broker spec may have lost its "
            "`security` requirement (introspection failing open):\n  " + "\n  ".join(sorted(leaked))
        )


def collect_endpoints(
    control_app: FastAPI,
    broker_app: FastAPI | None = None,
) -> list[Endpoint]:
    """Collect control-plane + broker endpoints from live apps.

    Both apps are introspected directly — no OpenAPI vendor extensions are read.

    The broker runs as a separate process, so most callers (notably the live
    ``GET /reference/endpoints.json`` handler, mounted only on the control-plane
    app) cannot pass a ``broker_app``. In that case the broker surface is taken
    from :func:`_declared_broker_endpoints` so the live reference still covers the
    broker and stays identical to the committed ``docs/reference/endpoints.json``
    (which the offline tool builds by introspecting a real broker app). A drift
    test pins the declaration to the real broker app.
    """
    control = _endpoints_from_app(control_app, "control")
    if broker_app is not None:
        broker = _endpoints_from_app(broker_app, "broker")
        _annotate_broker(broker)
    else:
        broker = _declared_broker_endpoints()

    endpoints = _dedupe(control + broker)
    assert_classification_is_sound(control, PUBLIC_OPERATION_IDS)
    assert_broker_classification_is_sound(broker)
    return endpoints


def build_reference_payload(endpoints: list[Endpoint]) -> dict[str, Any]:
    """The portable, stack-agnostic JSON payload (committed file + HTTP endpoint).

    Identical shape whether produced offline by ``make endpoints`` or served live
    by ``GET /reference/endpoints.json`` — both call this with the same endpoints.

    The ``scopes`` section is the conceptual scope catalogue (meaning + implication
    graph) built from the permission source of truth, so the docs SPA can render
    "what each scope means" alongside "which endpoints need it" from one document.
    """
    return {
        "schema": REFERENCE_SCHEMA,
        "total": len(endpoints),
        "groups": list(GROUP_ORDER),
        "endpoints": [asdict(ep) | {"group": ep.group} for ep in endpoints],
        "scopes": build_scope_catalog(),
    }
