"""Endpoint → actor-type → scope join for authorization metadata.

This module derives, for every operation in the control-plane app, the **actor
types** that may call it and the **scope(s)** it requires, and turns that into a
richer, advisory join (typical caller, auth notes, implied-scope closure)
consumed by :mod:`jentic_one.shared.web.endpoint_reference` to build the CLI /
docs reference — see :func:`build_operation_auth_map`.

The OpenAPI document itself only models the real authentication mechanism
(``BearerAuth``); the scope/actor join is *not* expressed as native OpenAPI
``security`` because OpenAPI's model can't faithfully carry it (OR-of-scopes,
the ``org:admin`` superuser bypass, the typical-caller hint, and the fact that
many scopes are enforced in the service layer rather than at the gateway). A
fabricated OAuth2 flow would misrepresent that, so the canonical authz reference
lives in the endpoint reference / ``GET /reference/endpoints.json`` sidecar that
the CLI and docs SPA actually consume.

Why a curated map exists
------------------------
Most scope checks for the control plane happen in the *service layer*, not at the
FastAPI dependency, so the route object alone does not know the required scope
(see ``docs/plans/issue-529-endpoint-scope-tree.md`` §2c). For those operations we
fall back to :data:`PATH_SCOPE_OVERRIDES` / :data:`ACTOR_TYPE_OVERRIDES` below.

Contributing (humans **and** agents)
------------------------------------
This map is the human-editable source of truth for the endpoint reference. To
correct or enrich an entry:

1. Edit :data:`PATH_SCOPE_OVERRIDES` / :data:`ACTOR_TYPE_OVERRIDES` here (or, better,
   add ``required_permissions=[...]`` to the route's ``get_current_identity(...)``
   so it becomes recoverable without curation).
2. Run ``make endpoints`` to regenerate ``docs/reference/endpoints.{md,json}`` and
   ``make openapi`` to regenerate the specs.
3. Commit the code change **and** the regenerated artifacts together.

Never hand-edit the generated files under ``docs/reference/`` — they carry a
``DO NOT EDIT`` header and are guarded by a drift test.
"""

from __future__ import annotations

import re
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.routing import APIRoute

from jentic_one.shared.auth.permission_catalog import compute_implies_transitive
from jentic_one.shared.models.actors import ActorType
from jentic_one.shared.scopes import AGENTS_WRITE, DEFAULT_AGENT_SCOPES

_log = structlog.get_logger(__name__)

#: FastAPI path converters (``{api_id:path}``) are stripped to ``{api_id}`` in the
#: generated OpenAPI document, so the route path must be normalised before it can
#: be joined to the spec by ``(method, path)``.
_PATH_CONVERTER_RE = re.compile(r"\{([^}:]+):[^}]+\}")


def _normalise_path(path: str) -> str:
    """Drop FastAPI path-converter suffixes so the path matches the OpenAPI key."""
    return _PATH_CONVERTER_RE.sub(r"{\1}", path)


try:  # upstream FastAPI 0.138.0 wraps included routers in this private class
    from fastapi.routing import _IncludedRouter as _IncludedRouterType
except ImportError:  # pragma: no cover - guards against a future FastAPI refactor
    # This is the canary for a FastAPI bump that changed how included routers are
    # represented in ``app.routes``. Without the wrapper class we can only see the
    # top-level routes, so the auth map would come back nearly empty — the
    # downstream ``assert_classification_is_sound`` guard turns that into a hard
    # failure, but log here so the *cause* (not just the symptom) is visible.
    _IncludedRouterType = None  # type: ignore[assignment,misc]
    _log.warning(
        "fastapi.routing._IncludedRouter is unavailable — route introspection for "
        "the endpoint/scope reference may be incomplete (check the FastAPI version)."
    )


# --- actor-type vocabulary --------------------------------------------------

_ALL_ACTORS: tuple[str, ...] = tuple(a.value for a in ActorType)


# --- typical-caller hint (NON-binding guidance, NOT enforcement) ------------
#
# ``actor_types`` / the OpenAPI ``security`` requirement above are derived purely
# from what the code *enforces* (the route dependency only checks the scope, not
# the actor kind), so for most routes they honestly say "any authenticated actor
# that holds the scope". That is correct but low-signal for a human skimming the
# reference. ``typical_caller`` is a separate, clearly-labelled *hint* at who
# usually calls an endpoint, inferred from the scope family. It is advisory only:
# a service account granted ``users:write`` really can call an operator endpoint.
# Never use this to gate a request.

TYPICAL_AGENT = "agent"
TYPICAL_OPERATOR = "operator"
TYPICAL_ANY = "any"

#: Actors that ride the programmatic (agent) token flow rather than a human login.
_PROGRAMMATIC_ACTORS: frozenset[str] = frozenset(
    {ActorType.AGENT.value, ActorType.SERVICE_ACCOUNT.value, ActorType.TOOLKIT.value}
)

#: Scopes an agent is granted by default — endpoints needing only these are
#: *typically* called by agents/service accounts.
_AGENT_DEFAULT_SCOPES: frozenset[str] = frozenset(DEFAULT_AGENT_SCOPES)

#: Scopes that are typically held by a human operator / admin console rather than
#: an autonomous agent (agents are never granted these by default).
_OPERATOR_SCOPES: frozenset[str] = frozenset(
    {
        "org:admin",
        "users:read",
        "users:write",
        "agents:read",
        "agents:write",
        "service-accounts:read",
        "service-accounts:write",
        "audit:read",
        "events:write",
        # Confirming an overlay rewrites the served spec — a human operator action,
        # never an agent default (excluded from GRANTABLE_SCOPES/DEFAULT_AGENT_SCOPES).
        "overlays:confirm",
    }
)


def _typical_caller(scopes: list[str], actor_types: list[str]) -> str:
    """Best-effort hint at who usually calls an endpoint (advisory, not enforced).

    - An explicit single-actor restriction wins (e.g. a service-account-only route).
    - Operator-family scope -> ``operator``.
    - Only agent-default scopes -> ``agent``.
    - Otherwise (no scope, or a mixed/ordinary scope) -> ``any``.
    """
    if actor_types == [ActorType.USER.value]:
        return TYPICAL_OPERATOR
    if actor_types and set(actor_types) <= _PROGRAMMATIC_ACTORS:
        # Route explicitly limited to programmatic actors.
        return TYPICAL_AGENT
    if not scopes:
        return TYPICAL_ANY
    if any(s in _OPERATOR_SCOPES for s in scopes):
        return TYPICAL_OPERATOR
    if all(s in _AGENT_DEFAULT_SCOPES for s in scopes):
        return TYPICAL_AGENT
    return TYPICAL_ANY


# --- curated overrides (the community/agent-PR surface) ---------------------

#: ``(method, path) -> [scope, ...]`` for operations whose scope is enforced in the
#: service layer (so it cannot be read off the route dependency). OR-semantics:
#: the caller needs *at least one* of the listed scopes. Keep keys in sync with
#: the generated spec's paths. ``method`` is upper-case; ``path`` is the OpenAPI
#: templated path (e.g. ``/credentials/{credential_id}``).
#:
#: Example (uncomment / adapt)::
#:
#:     PATH_SCOPE_OVERRIDES = {
#:         ("POST", "/access-requests"): ["agents:write"],
#:     }
PATH_SCOPE_OVERRIDES: dict[tuple[str, str], list[str]] = {
    # Access requests: only the *decide* (fulfil) path enforces a scope —
    # AccessRequestService._compute_evaluation requires ``agents:write`` (or
    # ``org:admin``, which implies it) before a request can be approved. Filing,
    # listing, getting, amending and withdrawing are *not* scope-gated; they are
    # authorised by ownership/binding checks in the service layer, so they stay
    # bare-authenticated here (no scope override). See
    # control/services/access_requests/service.py.
    ("POST", "/access-requests/{request_id}:decide"): [AGENTS_WRITE],
}

#: ``(method, path) -> [actor_type, ...]`` to override the inferred actor types.
ACTOR_TYPE_OVERRIDES: dict[tuple[str, str], list[str]] = {
    # Session refresh is enforced in the service layer (AuthService.refresh
    # rejects non-user actors and opaque tokens), which the route dependency
    # cannot express — without this override the reference would read "any
    # authenticated actor" for a users-only operation.
    ("POST", "/auth/refresh"): [ActorType.USER.value],
}

#: ``(method, path) -> note`` for operations that **do** authenticate but not via
#: the standard ``get_current_identity`` bearer dependency (so closure
#: introspection cannot see them). Forces ``authenticated=True`` and records the
#: non-standard credential as an ``auth_note`` (surfaced in the endpoint
#: reference, not the spec) so the reference does not mislabel them as public.
#: Example: RFC 7592 registration management, gated by a
#: Registration-Access-Token rather than a platform JWT.
NON_IDENTITY_AUTH: dict[tuple[str, str], str] = {
    ("GET", "/register/{agent_id}"): (
        "Authenticated with the Registration-Access-Token issued at registration "
        "(RFC 7592), not a platform bearer token."
    ),
}


# --- route introspection ----------------------------------------------------


def _iter_api_routes(routes: list[Any], prefix: str = "") -> list[tuple[str, APIRoute]]:
    """Flatten ``app.routes`` into ``(prefix, route)`` pairs.

    Descends into upstream ``_IncludedRouter`` wrappers, accumulating the
    ``include_router(prefix=...)`` prefix so a route's *full* path can be
    reconstructed. FastAPI stores each included route's ``.path`` **without** the
    prefix its parent applied (the prefix lives on the ``_IncludedRouter`` wrapper,
    in ``include_context.prefix``), so a naive walk keys routes by their
    prefix-stripped path — which then fails to join to the OpenAPI document (whose
    paths carry the full prefix). That silently dropped every prefixed
    ``AppContainer.extra_routers`` route (e.g. an enterprise overlay's
    ``/enterprise/admin/*`` console) from the auth map, so the endpoint reference
    mis-classified them as public. Threading the prefix through fixes the join.
    """
    out: list[tuple[str, APIRoute]] = []
    for route in routes:
        if isinstance(route, APIRoute):
            out.append((prefix, route))
        elif _IncludedRouterType is not None and isinstance(route, _IncludedRouterType):
            # The included prefix lives on the wrapper's include_context (FastAPI
            # ≥0.138). Read it defensively: if a future refactor moves it, we fall
            # back to no extra prefix rather than crash — the classification guard
            # in endpoint_reference remains the loud backstop.
            context = getattr(route, "include_context", None)
            child_prefix = prefix + getattr(context, "prefix", "")
            out.extend(_iter_api_routes(route.original_router.routes, child_prefix))
    return out


#: Fully-qualified identity of the dependency closure produced by
#: :func:`jentic_one.shared.web.deps.get_current_identity`. We match on this exact
#: ``(module, qualname)`` pair rather than on the free-variable *names* alone:
#: this is a security classification, so an unrelated dependency factory that
#: merely happens to close over a ``required_permissions`` / ``require_actor_type``
#: variable must NOT be mistaken for the identity dependency.
_IDENTITY_DEP_MODULE = "jentic_one.shared.web.deps"
_IDENTITY_DEP_QUALNAME = "get_current_identity.<locals>._dependency"


def _is_identity_dependency(call: Any) -> bool:
    """True iff ``call`` is the closure returned by ``get_current_identity``.

    Identified by its ``__module__`` + ``__qualname__`` so a same-named free
    variable on some other dependency factory cannot be misclassified as the
    authentication dependency.

    Caveat: this assumes ``_dependency`` is a bare closure (no ``functools.wraps``,
    decorator, or ``partial`` rewriting its ``__qualname__``). That holds today; if
    it ever changes this would false-negative (routes wrongly read as public) — the
    ``assert_classification_is_sound`` count guard in
    :mod:`jentic_one.shared.web.endpoint_reference` is the backstop that turns such
    a regression into a loud failure rather than a silent one.
    """
    return (
        getattr(call, "__module__", None) == _IDENTITY_DEP_MODULE
        and getattr(call, "__qualname__", None) == _IDENTITY_DEP_QUALNAME
    )


def _closure_values(call: Any) -> tuple[bool, list[str] | None, ActorType | None]:
    """Read identity-dep info off a dep closure.

    Returns ``(is_identity_dep, required_permissions, require_actor_type)``. The
    identity dependency is recognised by its module/qualname (see
    :func:`_is_identity_dependency`); a bare ``get_current_identity()`` with no
    scope still authenticates, so ``perms``/``actor`` may both be ``None`` while
    ``is_identity_dep`` is ``True``.
    """
    if not _is_identity_dependency(call):
        return False, None, None
    code = getattr(call, "__code__", None)
    closure = getattr(call, "__closure__", None)
    if code is None or closure is None:  # pragma: no cover - identity dep always closes
        return False, None, None
    names = code.co_freevars
    cells = {name: cell.cell_contents for name, cell in zip(names, closure, strict=True)}
    perms = cells.get("required_permissions")
    actor = cells.get("require_actor_type")
    return (
        True,
        (list(perms) if perms else None),
        (actor if isinstance(actor, ActorType) else None),
    )


def _route_auth(route: APIRoute) -> tuple[bool, list[str] | None, ActorType | None]:
    """Return ``(has_identity, required_permissions, require_actor_type)`` for a route.

    If multiple identity dependencies are present (not currently the case for any
    route), the first scope/actor encountered wins, so the result is stable rather
    than dependent on traversal order.
    """
    has_identity = False
    perms: list[str] | None = None
    actor: ActorType | None = None
    dependant = route.dependant
    stack = list(dependant.dependencies) if dependant else []
    while stack:
        dep = stack.pop()
        is_identity, d_perms, d_actor = _closure_values(dep.call)
        if is_identity:
            has_identity = True
            if d_perms and perms is None:
                perms = d_perms
            if d_actor and actor is None:
                actor = d_actor
        stack.extend(dep.dependencies)
    return has_identity, perms, actor


def build_operation_auth_map(
    app: FastAPI,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Map ``(METHOD, path)`` to its recovered/curated ``{scopes, actor_types, authenticated}``.

    Keyed by ``(method, path)`` rather than ``operationId`` because FastAPI's
    generated ``operationId`` (e.g. ``createToolkit``) does not match the route's
    ``unique_id`` (e.g. ``create_toolkit_toolkits_post``); the path+method pair is
    stable across both the route table and the generated document.
    """
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for prefix, route in _iter_api_routes(app.routes):
        methods = {m.upper() for m in (route.methods or set())} - {"HEAD", "OPTIONS"}
        if not methods:
            continue
        has_identity, perms, actor = _route_auth(route)

        for method in methods:
            key = (method, _normalise_path(prefix + route.path))
            # A curated scope/actor override (or a non-identity auth note) makes an
            # operation authenticated even when no get_current_identity dependency
            # is visible (service-layer-enforced scopes, RAT-gated routes, ...).
            curated = (
                key in PATH_SCOPE_OVERRIDES
                or key in ACTOR_TYPE_OVERRIDES
                or key in NON_IDENTITY_AUTH
            )
            authenticated = has_identity or curated
            scopes: list[str] = list(PATH_SCOPE_OVERRIDES.get(key, perms or []))
            if key in ACTOR_TYPE_OVERRIDES:
                actor_types = list(ACTOR_TYPE_OVERRIDES[key])
            elif actor is not None:
                actor_types = [actor.value]
            else:
                actor_types = _inferred_actor_types()

            entry: dict[str, Any] = {
                "authenticated": authenticated,
                "scopes": scopes,
                "actor_types": actor_types if authenticated else [],
            }
            if authenticated:
                entry["typical_caller"] = _typical_caller(scopes, actor_types)
            note = NON_IDENTITY_AUTH.get(key)
            if note:
                entry["auth_note"] = note
            result[key] = entry
    return result


def _inferred_actor_types() -> list[str]:
    """The actor types that can reach an operation with no explicit actor restriction.

    The route dependency only checks the *scope*, never the actor kind (see
    ``shared/web/deps.py`` — ``get_current_identity`` enforces ``required_permissions``
    or ``org:admin``, not the actor type). A scope grant is likewise not restricted
    by actor type, so the honest answer for any operation that does not *explicitly*
    set ``require_actor_type`` is "any authenticated actor that holds the scope".

    We therefore return all actor types; the required scope (shown alongside) is the
    real gate. Endpoints genuinely limited to one actor kind set ``require_actor_type``
    on the route and are handled before this is reached (their actor comes from the
    dependency, not this inference).
    """
    return list(_ALL_ACTORS)


# --- scope closure ----------------------------------------------------------


def implied_scopes(scopes: list[str]) -> dict[str, list[str]]:
    """For each direct scope, its transitive implied closure (explanatory only)."""
    return {scope: sorted(compute_implies_transitive(scope)) for scope in scopes}
