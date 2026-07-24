"""Same-origin SPA static serving for the admin surface.

The bundle is packaged into the wheel at ``jentic_one/static`` via
``pyproject.toml`` ``force-include`` — the same mechanism used for the bundled
OpenAPI specs. At runtime we resolve that directory with
``importlib.resources`` and, when present, serve it via FastAPI's first-class
:meth:`FastAPI.frontend` so the admin surface serves the SPA same-origin (no
CORS). When running from a source checkout (``make dev``) the packaged copy
does not exist, so we fall back to the repo's ``ui/dist`` directly — no manual
``ui/dist`` → ``src/jentic_one/static`` copy step is needed.

The bundle is mounted under :data:`SPA_MOUNT_PATH` (``/app``), NOT the site
root. This is the key namespace-isolation property: the SPA owns ``/app`` and
``/app/*`` exclusively, so *anything outside ``/app`` is unambiguously a
backend call*. An unknown non-``/app`` path is a plain 404 regardless of the
``Accept`` header — there is no content-negotiation guesswork and no
hand-maintained "API-owned prefix" allow-list. Within ``/app/*`` the
``fallback="auto"`` behaviour still applies so SPA deep-link refreshes
(``/app/agents/123``) serve ``index.html``.

``app.frontend()`` registers the bundle as *low-priority* routes: real API
path operations are matched first, and the static files are only consulted when
no API route matched. The bare site root (``/``) 307-redirects to
:data:`SPA_MOUNT_PATH` so a human typing the host lands in the app. Root icon
probes the browser/OS hardcodes to the site root (``/favicon.ico``,
``/apple-touch-icon*.png``) likewise 307-redirect into ``/app`` so a fresh
visit produces no console 404 (issue #614).

When no bundle is present (local dev without a UI build, or a placeholder
``dist`` containing only ``.gitkeep``) nothing is registered and the surface
behaves exactly as an API-only app.

The admin health endpoint lives at a *different* path depending on deploy mode:
``/health`` standalone (``create_surface_app`` drops the surface prefix) versus
``/admin/health`` combined (the root app keeps prefixes so surfaces don't
collide). The serving layer is the only component that knows which mode it's
in, so it exposes that path to the SPA via a tiny JSON config endpoint
(``GET /app-config.json``) the SPA fetches on boot — replacing the older
``index.html`` HTML-rewrite. The bundle is served byte-for-byte as built.
"""

from __future__ import annotations

import importlib.resources
from collections.abc import Awaitable, Callable
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse

_logger = structlog.get_logger(__name__)

# URL prefix the SPA bundle is mounted under. Everything outside this prefix is
# a backend route; an unknown path under neither a real router nor this prefix
# is a true 404. Kept in lockstep with the UI's Vite ``base`` and React Router
# ``basename`` (both ``/app`` — see ``ui/vite.config.ts`` / ``ui/src/main.tsx``).
SPA_MOUNT_PATH = "/app"

# Fixed, mode-independent path the SPA fetches on boot to learn deploy-mode
# facts (currently just the admin health path). Served at the site root (NOT
# under ``/app``) so it stays a stable absolute URL the SPA can fetch before the
# router is even mounted; the *value* it returns is what varies by deploy mode.
# Kept in sync with the UI bootstrap in ``ui/src/shared/config.ts``.
APP_CONFIG_PATH = "/app-config.json"

# Icon filenames the browser/OS probes at the *site root* (ignorant of the /app
# base): a fresh visit auto-requests ``/favicon.ico`` and iOS "Add to Home
# Screen" probes ``/apple-touch-icon*.png``. Each maps to the real bundled asset
# under the SPA mount that should answer it; :func:`mount_spa` 307-redirects the
# root probe to that ``/app`` target (issue #614). ``-precomposed`` is the legacy
# iOS spelling of the touch icon and has no dedicated asset, so it points at the
# same ``apple-touch-icon.png`` rather than 404-ing on a file we never generate.
_ROOT_ICON_PROBES = {
    "favicon.ico": "favicon.ico",
    "apple-touch-icon.png": "apple-touch-icon.png",
    "apple-touch-icon-precomposed.png": "apple-touch-icon.png",
}


def _repo_root() -> Path:
    """Repo root of a source checkout, four parents up from this module.

    ``src/jentic_one/shared/web/static.py`` → repo root. In a wheel install this
    still resolves to *some* directory under ``site-packages``; callers guard
    against that via the ``pyproject.toml`` check in :func:`_resolve_dev_static_dir`.
    """
    return Path(__file__).resolve().parents[4]


def _resolve_dev_static_dir(repo_root: Path | None = None) -> Path | None:
    """Return ``ui/dist`` from a source checkout if it holds a built SPA.

    Dev-only fallback for running straight from the repo (``make dev`` /
    ``uv run python -m jentic_one``), where the SPA is **not** packaged under
    ``jentic_one/static`` — that copy only exists in the built wheel. The
    ``ui/dist`` bundle is produced by ``make ui-build`` and lives at the repo
    root. Resolving it here removes the former manual ``ui/dist`` →
    ``src/jentic_one/static`` symlink step.

    Returns ``None`` when the checkout layout is absent (e.g. a wheel install,
    where this module lives under ``site-packages`` and there is no sibling
    ``ui/dist``) or the bundle has not been built, so production/wheel serving
    is unaffected — the packaged ``static/`` in :func:`_resolve_static_dir`
    still takes precedence.
    """
    root = repo_root if repo_root is not None else _repo_root()
    if not (root / "pyproject.toml").is_file():
        # Not a source checkout (e.g. installed under site-packages): bail out
        # so wheel installs never accidentally resolve an unrelated directory.
        return None

    dist = root / "ui" / "dist"
    if not (dist / "index.html").is_file():
        return None
    return dist


def _resolve_static_dir() -> Path | None:
    """Return the SPA bundle directory if one holds a built SPA, else None.

    Prefers the packaged ``jentic_one/static`` bundle (wheel/production); when
    that is absent, falls back to a source checkout's ``ui/dist`` so a dev run
    from the repo serves the SPA without a manual copy/symlink step.
    """
    try:
        static_root = importlib.resources.files("jentic_one") / "static"
    except (ModuleNotFoundError, FileNotFoundError):
        static_root = None

    if static_root is not None and (static_root / "index.html").is_file():
        # ``files()`` may return a non-filesystem traversable; ``app.frontend``
        # needs a real directory path. The wheel install is always on the
        # filesystem.
        return Path(str(static_root))

    return _resolve_dev_static_dir()


def mount_spa(app: FastAPI, *, health_path: str = "/health") -> bool:
    """Serve the built SPA on ``app`` if a bundle is packaged.

    ``health_path`` is the admin health endpoint for the current deploy mode
    (``/health`` standalone, ``/admin/health`` combined). It is exposed to the
    SPA at runtime via :data:`APP_CONFIG_PATH` so the SPA hits the correct URL
    either way.

    Returns True when the SPA was registered. Safe to call on any surface; only
    the admin surface is expected to call it. No-op (returns False) when no
    bundle is present, leaving the app API-only.

    Call ordering does not affect correctness: ``app.frontend()`` routes are
    *low-priority* (consulted only after every normal route fails to match), so
    real API paths are never shadowed regardless of when this runs relative to
    router registration.
    """
    static_dir = _resolve_static_dir()
    if static_dir is None:
        _logger.info("spa_not_mounted_no_bundle")
        return False

    # Runtime config the SPA fetches on boot. A normal (high-priority) route, so
    # it always wins over the low-priority frontend fallback for this exact path.
    @app.get(APP_CONFIG_PATH, include_in_schema=False)
    async def app_config() -> JSONResponse:
        return JSONResponse({"healthPath": health_path})

    # The SPA lives under /app; the bare root is not an SPA route. Send a human
    # who typed the host straight into the app. A normal (high-priority) route,
    # so it is never shadowed by the low-priority frontend fallback.
    @app.get("/", include_in_schema=False)
    async def root_redirect() -> RedirectResponse:
        return RedirectResponse(f"{SPA_MOUNT_PATH}/", status_code=307)

    # Root icon probes the browser/OS hardcodes to the site root, ignorant of
    # the /app base (issue #614): a fresh visit auto-requests ``GET /favicon.ico``
    # and iOS "Add to Home Screen" probes ``/apple-touch-icon*.png``. The bytes
    # live under the SPA mount, so 307-redirect each probe to its real /app
    # target (see :data:`_ROOT_ICON_PROBES`) — no console 404, no duplicated
    # assets. The in-document <link rel> tags in index.html already point at /app
    # directly; these routes only catch the implicit root probes.
    def _make_icon_redirect(target: str) -> Callable[[], Awaitable[RedirectResponse]]:
        async def _icon_redirect() -> RedirectResponse:
            return RedirectResponse(f"{SPA_MOUNT_PATH}/{target}", status_code=307)

        return _icon_redirect

    for _probe, _target in _ROOT_ICON_PROBES.items():
        app.add_api_route(
            f"/{_probe}",
            _make_icon_redirect(_target),
            methods=["GET", "HEAD"],
            include_in_schema=False,
        )

    # Serve the bundle under /app. Because the frontend group only matches paths
    # under its mount prefix, anything outside /app never reaches this handler:
    # unknown non-/app paths 404 for every client (no Accept-header guesswork).
    # Within /app/*, ``fallback="auto"`` serves index.html for navigation
    # requests so SPA deep-link refreshes (/app/agents/123) work, while a
    # non-navigation request (e.g. ``Accept: application/json`` or a path with a
    # file extension) gets a 404. Every real API client sends JSON, but real API
    # routes never live under /app anyway, so this only governs unknown /app/*
    # subpaths (always SPA routes in practice).
    app.frontend(SPA_MOUNT_PATH, directory=str(static_dir), fallback="auto")

    _logger.info(
        "spa_mounted",
        static_dir=str(static_dir),
        mount_path=SPA_MOUNT_PATH,
        health_path=health_path,
    )
    return True
