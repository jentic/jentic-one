"""Route coverage for the split-deploy gateway (#1418).

In the parts topology each surface runs as its own process and an nginx gateway
maps path prefixes to them. The map is hand-written twice — the Docker
reference config and the Helm gateway configmap — so a new router can ship with
no gateway location (its routes 404 at the gateway) or land under a shorter
prefix that belongs to another surface (``/oauth`` would send
``/oauth-app-registrations`` to admin).

This gate builds every backend the gateway fronts exactly as its image runs it
(``JENTIC__APPS`` from ``deploy/docker/<backend>.Dockerfile``, assembled by the
real composition root), walks its route table, and resolves each path against
both gateway configs with nginx's location-selection rules. Every path must land
on a backend that serves it, every proxied location must serve at least one
path, and the two configs must carry the same location map.

The images always ship the built SPA (``python-base.Dockerfile`` builds the UI
into the wheel), so each backend is built with a stand-in bundle whether or not
the checkout has a local ``ui/dist``: the gate checks the production route
table and gives the same answer either way (#1688).
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from starlette.routing import Route

from jentic_one.__main__ import _build_app, _build_context
from jentic_one.mcp.app import McpChallengePlaceholder
from jentic_one.shared.config import load_config
from jentic_one.shared.web import static

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

DOCKER_GATEWAY_CONF = REPO_ROOT / "deploy" / "docker" / "nginx" / "parts-gateway.conf"
HELM_GATEWAY_CONFIGMAP = (
    REPO_ROOT / "deploy" / "helm" / "jentic-one" / "charts" / "gateway" / "templates"
) / "configmap.yaml"
DOCKERFILE_DIR = REPO_ROOT / "deploy" / "docker"

#: The SQLite config has no external dependencies; building an app never opens
#: a database connection (same posture as ``tools/openapi_export``). Passed to
#: ``load_config`` explicitly so the shell's ``JENTIC_CONFIG_FILE`` never applies.
DEFAULT_CONFIG = REPO_ROOT / "config" / "local-sqlite.yaml"

_UPSTREAM_RE = re.compile(r"upstream\s+(\w+)_backend\s*\{")
_LOCATION_RE = re.compile(r"location\s+(?:(=|\^~|~\*|~)\s+)?(\S+)\s*\{([^}]*)\}")
#: The whole directive, ``;`` included: ``proxy_pass http://x_backend/;`` (any
#: URI part) would forward a rewritten path, so it must not match.
_PROXY_PASS_RE = re.compile(r"proxy_pass\s+http://(\w+)_backend\s*;")
_APPS_ENV_RE = re.compile(r"^ENV\s+JENTIC__APPS=(\S+)", re.MULTILINE)
_PATH_PARAM_RE = re.compile(r"\{[^}]+\}")

#: Paths the gateway answers itself (its own ``location = /health``) rather than
#: proxying, so no backend's copy is reachable or needs to be.
_GATEWAY_OWNED_PATHS = frozenset({"/health"})


@dataclass(frozen=True)
class Location:
    """One nginx ``location`` block: its match modifier, path and upstream."""

    modifier: str  # "=" (exact) or "" (prefix)
    path: str
    backend: str | None  # ``<name>`` of ``<name>_backend``; None when not proxied

    def __str__(self) -> str:
        head = f"location {self.modifier} {self.path}" if self.modifier else f"location {self.path}"
        return f"{head} -> {self.backend or '(gateway)'}"


@dataclass(frozen=True)
class GatewayConfig:
    """The parsed server block of one gateway config file."""

    source: Path
    backends: frozenset[str]
    locations: tuple[Location, ...]

    def resolve(self, path: str) -> Location | None:
        """Select the location nginx serves ``path`` from.

        An exact (``=``) match wins outright; otherwise the longest matching
        prefix wins. Regex locations would make the order of blocks matter, so
        :func:`_parse_gateway` refuses them rather than modelling them.
        """
        for loc in self.locations:
            if loc.modifier == "=" and loc.path == path:
                return loc
        prefixes = [
            loc for loc in self.locations if loc.modifier == "" and path.startswith(loc.path)
        ]
        return max(prefixes, key=lambda loc: len(loc.path), default=None)


def _parse_gateway(source: Path, text: str) -> GatewayConfig:
    locations: list[Location] = []
    for modifier, path, body in _LOCATION_RE.findall(text):
        head = f"location {modifier} {path}" if modifier else f"location {path}"
        if modifier not in ("", "="):
            raise AssertionError(
                f"{source}: `{head}` — the gateway map uses only exact (=) "
                "and plain prefix locations; regex/^~ locations change nginx's selection order "
                "and are not modelled by this gate"
            )
        proxy = _PROXY_PASS_RE.search(body)
        if proxy is None and "proxy_pass" in body:
            raise AssertionError(
                f"{source}: `{head}` — proxy_pass must be exactly "
                "`proxy_pass http://<name>_backend;`; a URI part (even a trailing `/`) makes "
                "nginx replace the matched location prefix, so the backend sees a rewritten path"
            )
        locations.append(Location(modifier, path, proxy.group(1) if proxy else None))
    return GatewayConfig(
        source=source,
        backends=frozenset(_UPSTREAM_RE.findall(text)),
        locations=tuple(locations),
    )


def _helm_server_conf() -> str:
    """The nginx config embedded in the Helm configmap's ``default.conf`` block.

    Template expressions only appear in the upstream ``server`` lines, which the
    gate reads by upstream name, so the raw template text parses as-is.
    """
    text = HELM_GATEWAY_CONFIGMAP.read_text()
    marker = "default.conf: |"
    assert marker in text, f"{HELM_GATEWAY_CONFIGMAP}: no `{marker}` block"
    return text.split(marker, 1)[1]


@cache
def _gateways() -> tuple[GatewayConfig, ...]:
    return (
        _parse_gateway(DOCKER_GATEWAY_CONF, DOCKER_GATEWAY_CONF.read_text()),
        _parse_gateway(HELM_GATEWAY_CONFIGMAP, _helm_server_conf()),
    )


def _backend_apps(backend: str) -> list[str]:
    """The surfaces a backend's image serves (its baked ``JENTIC__APPS``)."""
    dockerfile = DOCKERFILE_DIR / f"{backend}.Dockerfile"
    assert dockerfile.is_file(), (
        f"gateway upstream `{backend}_backend` has no image at {dockerfile.relative_to(REPO_ROOT)}"
    )
    match = _APPS_ENV_RE.search(dockerfile.read_text())
    assert match, f"{dockerfile.relative_to(REPO_ROOT)}: no `ENV JENTIC__APPS=` line"
    return match.group(1).split(",")


def _iter_route_paths(routes: Iterable[Any], prefix: str = "") -> Iterator[str]:
    """Flatten a route table into full paths, descending into included routers.

    FastAPI wraps an included router in an ``_IncludedRouter`` whose child
    routes carry only their own path; the include prefix lives on the wrapper.
    """
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            context = getattr(route, "include_context", None)
            yield from _iter_route_paths(original.routes, prefix + getattr(context, "prefix", ""))
            continue
        if isinstance(route, Route) and isinstance(route.endpoint, McpChallengePlaceholder):
            # A discovery-challenge stand-in on shapes without control; the
            # real /mcp transport lives with control, so this copy is not a
            # route the gateway may send /mcp to.
            continue
        path = getattr(route, "path", None)
        if path:
            yield prefix + path


def _build_backend(apps: list[str], spa_bundle: Path) -> FastAPI:
    """Build a backend as its image runs it, with ``spa_bundle`` as the SPA.

    Pinning the bundle keeps the route table independent of whether this
    checkout has a built ``ui/dist`` (or a stale packaged ``jentic_one/static``).
    """
    config = load_config(DEFAULT_CONFIG).model_copy(update={"apps": apps})
    with patch.object(static, "_resolve_static_dir", return_value=spa_bundle):
        return _build_app(_build_context(config, apps), apps)


def _spa_paths(app: FastAPI) -> Iterator[str]:
    """The SPA bundle's own paths, when the backend mounts it.

    ``app.frontend()`` registers the bundle as low-priority routes outside
    ``app.routes``, so the route walk never sees them; the mount answers its
    bare prefix and everything below it.
    """
    if getattr(app.state, "spa_mounted", False):
        yield static.SPA_MOUNT_PATH
        yield f"{static.SPA_MOUNT_PATH}/x"


@cache
def _served_paths() -> dict[str, frozenset[str]]:
    """Map each concrete request path to the gateway backends that serve it."""
    backends = set().union(*(gateway.backends for gateway in _gateways()))
    served: dict[str, set[str]] = {}
    with tempfile.TemporaryDirectory() as bundle_dir:
        spa_bundle = Path(bundle_dir)
        (spa_bundle / "index.html").write_text("<!doctype html><title>stand-in</title>\n")
        for backend in sorted(backends):
            app = _build_backend(_backend_apps(backend), spa_bundle)
            for template in [*_iter_route_paths(app.routes), *_spa_paths(app)]:
                # Any concrete value exercises the same location selection as
                # the template; ``x`` keeps ``:verb`` suffixes and sub-paths intact.
                served.setdefault(_PATH_PARAM_RE.sub("x", template), set()).add(backend)
    return {path: frozenset(owners) for path, owners in served.items()}


def _gateway_ids() -> list[str]:
    return [str(gateway.source.relative_to(REPO_ROOT)) for gateway in _gateways()]


@pytest.mark.arch
@pytest.mark.parametrize("gateway", _gateways(), ids=_gateway_ids())
def test_every_backend_route_is_routed_to_a_backend_that_serves_it(
    gateway: GatewayConfig,
) -> None:
    """Each path a fronted backend serves resolves to a location proxying to it.

    Catches both an unrouted router (gateway 404) and a shadowed one (a shorter
    prefix owned by another surface swallows it).
    """
    violations: list[str] = []
    for path, owners in sorted(_served_paths().items()):
        if path in _GATEWAY_OWNED_PATHS:
            continue
        loc = gateway.resolve(path)
        expected = " or ".join(f"{owner}_backend" for owner in sorted(owners))
        if loc is None:
            violations.append(f"{path} — no location matches (expected {expected})")
        elif loc.backend not in owners:
            violations.append(f"{path} — `{loc}` catches it (expected {expected})")
    assert not violations, (
        f"{gateway.source.relative_to(REPO_ROOT)} does not route every backend path to a "
        "backend that serves it. Add (or lengthen) a location for each path below in BOTH "
        "deploy/docker/nginx/parts-gateway.conf and the Helm gateway configmap:\n  "
        + "\n  ".join(violations)
    )


@pytest.mark.arch
@pytest.mark.parametrize("gateway", _gateways(), ids=_gateway_ids())
def test_every_proxied_location_serves_a_backend_route(gateway: GatewayConfig) -> None:
    """A proxied location no backend path resolves to is dead config (#1418)."""
    used = {gateway.resolve(path) for path in _served_paths()}
    dead = [str(loc) for loc in gateway.locations if loc.backend and loc not in used]
    assert not dead, (
        f"{gateway.source.relative_to(REPO_ROOT)} proxies locations that no backend route "
        "resolves to — the routes are gone, so delete the blocks from BOTH gateway "
        "configs:\n  " + "\n  ".join(dead)
    )


@pytest.mark.arch
def test_docker_and_helm_gateways_share_one_location_map() -> None:
    """The Docker reference config and the Helm configmap route identically."""
    docker, helm = _gateways()
    assert docker.backends == helm.backends, (
        f"upstreams differ: docker={sorted(docker.backends)} helm={sorted(helm.backends)}"
    )
    docker_map, helm_map = set(docker.locations), set(helm.locations)
    drift = [f"docker only: {loc}" for loc in sorted(docker_map - helm_map, key=str)] + [
        f"helm only:   {loc}" for loc in sorted(helm_map - docker_map, key=str)
    ]
    assert not drift, (
        "deploy/docker/nginx/parts-gateway.conf and the Helm gateway configmap have drifted; "
        "keep their location maps identical:\n  " + "\n  ".join(drift)
    )


@pytest.mark.arch
@pytest.mark.parametrize(
    "directive",
    ["proxy_pass http://control_backend/;", "proxy_pass http://control_backend/vendors;"],
)
def test_parser_refuses_a_proxy_pass_with_a_uri_part(directive: str) -> None:
    """A URI part on proxy_pass rewrites the forwarded path, so the gate refuses it."""
    conf = f"upstream control_backend {{ server c:8000; }}\nlocation /vendors {{ {directive} }}"
    with pytest.raises(AssertionError, match="proxy_pass must be exactly"):
        _parse_gateway(Path("stub.conf"), conf)
