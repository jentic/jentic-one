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
(``/app/agents/123``) serve ``index.html``. A dedicated HTML-navigation
fallback additionally covers *dotted* deep-links (e.g. an API-version segment
``/app/workspace/<vendor>/<name>/1.0``) that ``fallback="auto"``'s final-segment
extension heuristic would otherwise mis-classify as a static file and 404
(issue #647); real bundle files and non-navigation requests still resolve/404 as
before. That fallback distinguishes a *route* from an *asset* by whether the
final segment names a recognized static-file type (by :mod:`mimetypes`, so it
tracks real asset extensions instead of drifting from ``ui/vite.config.ts``),
not by a curated directory-prefix list — so a missing/renamed asset anywhere in
the bundle (``assets/…`` *or* a mount-root file like ``broker-openapi.json``)
still surfaces as a ``404`` rather than a silent app boot.

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

import contextlib
import email.message
import importlib.resources
import mimetypes
import os
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.middleware import Middleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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

def _accept_header(scope: Scope) -> str:
    """Return the request's combined ``Accept`` header value from an ASGI scope.

    Reads only the ``accept`` header (avoids materialising a full header dict on
    the hot SPA path) and RFC 7230-combines repeated header lines with commas —
    a client/intermediary may split ``Accept`` across multiple lines, and taking
    only the last would drop an earlier ``text/html`` and 404 a real navigation.
    """
    values = [
        value.decode("latin-1")
        for key, value in scope.get("headers", [])
        if key == b"accept"
    ]
    return ",".join(values)


def _iter_accept_media_types(accept: str) -> Iterator[tuple[str, float]]:
    """Yield ``(media_type, quality)`` pairs from an ``Accept`` header value.

    Mirrors FastAPI's own header parsing (``fastapi.routing``) so our navigation
    check agrees with the framework on what "the client accepts HTML" means.
    """
    for raw_value in accept.split(","):
        message = email.message.Message()
        message["content-type"] = raw_value.strip()
        q = message.get_param("q")
        quality = 1.0
        if isinstance(q, str):
            with contextlib.suppress(ValueError):
                quality = float(q)
        yield (
            f"{message.get_content_maintype()}/{message.get_content_subtype()}",
            quality,
        )


def _looks_like_static_asset(spa_path: str) -> bool:
    """True when the final path segment names a *recognized* static file type.

    Keys off :mod:`mimetypes` (``.json``, ``.svg``, ``.png``, ``.js``, ``.css``,
    ``.webmanifest``, …) rather than a hardcoded directory-prefix list, so it
    tracks real asset types instead of drifting from ``ui/vite.config.ts``. The
    bundle ships assets both under ``assets/`` *and* at the mount root
    (``broker-openapi.json``, ``favicon.svg``, ``site.webmanifest``, …), so a
    prefix list can't cover them; a MIME-mapped extension can.

    A missing/renamed asset of a recognized type must surface as a ``404`` (a
    broken deploy), never the SPA shell — so callers refuse the shell fallback
    for these. Version-like segments (``1.0`` → ``.0``, ``2.1.3`` → ``.3``) and
    unknown extensions are *not* recognized asset types and stay ambiguous
    (handled by the explicit-``text/html`` rule).
    """
    ext = os.path.splitext(spa_path.rsplit("/", 1)[-1])[1].lower()
    return bool(ext) and ext in mimetypes.types_map


def _has_extension(spa_path: str) -> bool:
    """True when the final path segment has *any* extension (dot-suffix).

    Matches FastAPI's ``fallback="auto"`` heuristic (``os.path.splitext`` on the
    last segment). Used to decide *how strict* the navigation check must be for
    a path that is not a recognized static asset: an extension-less path is
    unambiguously an app route, whereas a dotted-but-unrecognized final segment
    (e.g. an API version ``1.0``) is ambiguous and so demands an explicit
    ``text/html`` accept.
    """
    return bool(os.path.splitext(spa_path.rsplit("/", 1)[-1])[1])


def _is_html_navigation(accept: str, *, require_explicit_html: bool) -> bool:
    """True when the request is a genuine browser navigation (wants HTML).

    ``accept`` is the request's combined ``Accept`` header value (see
    :func:`_accept_header`). The dotted-final-segment case (issue #647) is the
    tricky one: FastAPI's ``fallback="auto"`` mis-reads a versioned deep-link
    (``/app/.../1.0``) as a static-file request and 404s it. We relax that — but
    *carefully*, to avoid turning every missing ``foo.json``/``bar.js`` into a
    silent app boot:

    * ``require_explicit_html=False`` (extension-less path, unambiguously a
      route): accept a browser navigation the way FastAPI does — explicit
      ``text/html`` **or** a ``*/*`` wildcard.
    * ``require_explicit_html=True`` (dotted final segment, ambiguous): require
      an **explicit** ``text/html`` (or ``application/xhtml+xml``) accept. Real
      top-level browser navigations always send ``text/html``; asset/XHR/CLI
      clients send ``*/*`` or a specific non-HTML type, so a missing dotted asset
      still 404s.
    """
    wildcard_accepted = False
    html_rejected = False
    for media_type, quality in _iter_accept_media_types(accept):
        if media_type in {"text/html", "application/xhtml+xml"}:
            if quality == 0:
                html_rejected = True
            else:
                return True
        elif media_type == "*/*" and quality != 0:
            wildcard_accepted = True
    if require_explicit_html:
        return False
    return wildcard_accepted and not html_rejected


class _SpaNavigationFallbackMiddleware:
    """Serve the SPA shell for a browser navigation the app 404'd (issue #647).

    A thin ASGI shim registered as the **innermost** user middleware (see
    :func:`mount_spa`), so the shell it rescues still flows back out through the
    request-id and telemetry middleware — the rescued deep-link boot carries
    ``x-request-id`` and is recorded as the ``200`` the client actually receives,
    not the inner ``404``.

    It delegates every request to the wrapped app untouched and only rewrites the
    response when **all** of these hold:

    * method is GET or HEAD,
    * the path is under the SPA mount (``/app/…``),
    * the wrapped app produced a ``404`` status, and
    * the request is a genuine browser navigation (see :func:`_is_html_navigation`),
      with the extra strictness for dotted final segments, and the final segment
      is *not* a recognized static-file type (by :mod:`mimetypes`) so a missing
      asset still 404s (no shell leak) — see :func:`_looks_like_static_asset`.

    In that one case the 404 is swapped for the SPA shell (``200`` +
    ``index.html``) so the client router can resolve the deep-link. This keeps
    the framework's ``app.frontend()`` static serving fully authoritative for
    real files (conditional ``304`` revalidation, ``Range``, directory index,
    symlink policy) — we never re-serve assets by hand; we only reinterpret a
    navigation miss.
    """

    def __init__(self, app: ASGIApp, *, index_file: Path, mount_prefix: str) -> None:
        self.app = app
        self.index_file = index_file
        self.mount_prefix = mount_prefix

    def _should_consider(self, scope: Scope) -> bool:
        if scope["type"] != "http":
            return False
        if scope["method"] not in ("GET", "HEAD"):
            return False
        # Compare against the *routing* path (root_path stripped): behind a
        # path-prefix proxy ``scope["path"]`` carries the mount prefix (e.g.
        # ``/console/app/…``) that Starlette's routing already strips, so a raw
        # ``startswith("/app/")`` would miss the deep-link and reintroduce #647.
        # Strip only on a segment boundary so ``/consoleX/…`` isn't mis-stripped.
        route_path = self._strip_root_path(scope)
        if not route_path.startswith(self.mount_prefix):
            return False
        spa_path = route_path[len(self.mount_prefix) :]
        # A recognized static asset (``.json``/``.svg``/``.png``/``.js``/… by
        # MIME type, at the mount root or under ``assets/``) that the app 404'd
        # is a missing/renamed file — it must stay a 404, never the SPA shell, so
        # a broken deploy surfaces as an error instead of silently booting the
        # app. Version-like/unknown extensions are not assets and stay ambiguous.
        if _looks_like_static_asset(spa_path):
            return False
        # Dotted-but-unrecognized final segments (e.g. an API version ``1.0``)
        # are ambiguous (asset vs. route) so require an explicit ``text/html``
        # accept; extension-less paths accept the looser ``*/*`` navigation form.
        return _is_html_navigation(
            _accept_header(scope), require_explicit_html=_has_extension(spa_path)
        )

    def _strip_root_path(self, scope: Scope) -> str:
        """Return ``scope['path']`` with any ``root_path`` prefix removed.

        Only strips on a path-segment boundary so a coincidental non-boundary
        prefix (``root_path='/console'`` vs ``path='/consoleX/…'``) is left
        intact rather than mangled to ``X/…``.
        """
        root_path: str = scope.get("root_path", "")
        path: str = scope["path"]
        if not root_path:
            return path
        if path == root_path:
            return "/"
        if path.startswith(root_path + "/"):
            return path[len(root_path) :]
        return path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._should_consider(scope):
            await self.app(scope, receive, send)
            return

        # Peek at the wrapped app's response start. If it's a 404 we serve the
        # shell instead; otherwise we replay the captured start and stream the
        # rest of the body through untouched (no buffering of real responses).
        started = False
        is_404 = False

        async def send_wrapper(message: Message) -> None:
            nonlocal started, is_404
            if message["type"] == "http.response.start" and not started:
                started = True
                is_404 = message["status"] == 404
                if is_404:
                    # Swallow the 404 start; the shell is sent after the wrapped
                    # app finishes (below).
                    return
                await send(message)
                return
            # Once we've decided to rescue a 404, drop *every* inner message
            # (body chunks, and defensively any trailers/pathsend), so nothing
            # from the discarded 404 response interleaves with the shell's start.
            if is_404:
                return
            await send(message)

        await self.app(scope, receive, send_wrapper)

        if not is_404:
            return

        # The bundle could be mid-swap / incomplete (atomic redeploy) — if the
        # shell isn't on disk, don't 500; replay a clean 404 instead.
        if not self.index_file.is_file():
            await _send_plain_404(send)
            return

        # Serve the shell as a full 200. Strip Range/If-Range so a navigation
        # that happens to carry a range (resumed nav, some prefetchers) gets the
        # whole shell, not a 206/416 partial that fails to boot the SPA. The
        # wrapped app already drained ``receive`` (request body consumed), so hand
        # FileResponse a receive that only reports a disconnect — otherwise its
        # disconnect-listener could await a drained stream and stall completion.
        shell_scope = dict(scope)
        shell_scope["headers"] = [
            (name, value)
            for name, value in scope.get("headers", [])
            if name not in (b"range", b"if-range")
        ]

        async def _disconnected_receive() -> Message:
            return {"type": "http.disconnect"}

        await FileResponse(self.index_file, status_code=200)(
            shell_scope, _disconnected_receive, send
        )


async def _send_plain_404(send: Send) -> None:
    """Emit a minimal ``404`` when the rescued shell is unexpectedly absent."""
    await send(
        {
            "type": "http.response.start",
            "status": 404,
            "headers": [(b"content-type", b"text/plain; charset=utf-8")],
        }
    )
    await send({"type": "http.response.body", "body": b"Not Found"})


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

    # HTML-navigation fallback for dotted deep-links (issue #647).
    #
    # ``app.frontend(fallback="auto")`` above already serves index.html for
    # extension-less deep-link navigations (``/app/agents/123``) and serves every
    # real bundle file with the framework's full refinements (conditional 304
    # revalidation, Range, directory index, symlink policy). But its navigation
    # heuristic treats any path whose final segment has a file extension as a
    # static-file request — so a versioned API deep-link like
    # ``/app/workspace/<vendor>/<name>/1.0`` (final segment ``1.0``) is mis-read
    # as a file and 404s even for a browser navigation.
    #
    # We close *only* that gap, and deliberately do NOT re-serve assets by hand:
    # a thin ASGI shim delegates everything to the app untouched and only
    # rewrites the response when ALL hold — GET/HEAD, path under ``/app/``, the
    # app produced a 404, and the request is a genuine browser navigation. In
    # that one case it swaps the 404 for the SPA shell (200) so the client router
    # can resolve the route. Real files (200/206/304), real API 404s, non-``/app``
    # paths, and non-navigation ``/app`` misses all pass through byte-for-byte.
    #
    # Registered as the **innermost** user middleware (appended, not
    # ``add_middleware`` which prepends): the request-id + telemetry middleware
    # are added earlier (outermost) in the app factory, so the rescued shell
    # flows back out through them — it carries ``x-request-id`` and is recorded as
    # the 200 the client receives, not the inner 404.
    index_file = static_dir / "index.html"

    # Idempotency: never stack a second shim (a duplicate would risk two
    # http.response.start on a rescued 404). If already mounted, no-op.
    if any(
        getattr(m, "cls", None) is _SpaNavigationFallbackMiddleware
        for m in app.user_middleware
    ):
        _logger.info("spa_navigation_fallback_already_mounted")
    else:
        app.user_middleware.append(
            Middleware(
                _SpaNavigationFallbackMiddleware,
                index_file=index_file,
                mount_prefix=f"{SPA_MOUNT_PATH}/",
            )
        )
        # Invalidate the (not-yet-built) stack so the appended entry is picked up;
        # Starlette rebuilds lazily on the first request. ``add_middleware``
        # prepends (outermost) — we append to land *inside* request-id/telemetry.
        # Assert the stack hasn't been built yet: nulling a built stack would
        # silently discard it and re-open Starlette's "add after start" guard.
        assert app.middleware_stack is None, (
            "SPA fallback must be mounted before the middleware stack is built"
        )

    _logger.info(
        "spa_mounted",
        static_dir=str(static_dir),
        mount_path=SPA_MOUNT_PATH,
        health_path=health_path,
    )
    return True
