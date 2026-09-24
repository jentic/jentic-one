"""DB-free packaging smoke check for the bundled SPA.

Run against an *installed* ``jentic_one`` wheel (in a clean venv) to verify the
UI↔backend seam that no other test exercises end to end:

1. **Packaging** — the built UI bundle is force-included into the wheel and is
   reachable at runtime via ``importlib.resources.files("jentic_one")/"static"``.
2. **Serving** — ``mount_spa`` finds that bundle, 307-redirects the bare root to
   ``/app/``, serves ``index.html`` for the SPA mount and client-routed deep
   links under ``/app/`` (navigation requests), while unknown paths outside the
   ``/app`` namespace still return a 404 (the native ``app.frontend()`` mount
   must not shadow the API), and the runtime config endpoint reports the
   deploy-mode health path.

This deliberately does **not** boot the app's lifespan, so it needs no Postgres:
SPA static serving is independent of the database. Real browser/DB end-to-end
coverage is a separate, heavier concern for when there are actual UI flows.

Exits non-zero on the first failed assertion so CI fails loudly.
"""

from __future__ import annotations

import importlib.resources
import sys

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from jentic_one.shared.web.agent_discovery import get_agent_discovery_router
from jentic_one.shared.web.static import APP_CONFIG_PATH, SPA_MOUNT_PATH, mount_spa


def _check_packaged_bundle() -> None:
    static_root = importlib.resources.files("jentic_one") / "static"
    index = static_root / "index.html"
    if not index.is_file():
        raise AssertionError(
            f"UI bundle not packaged: {index} missing. "
            "The wheel must force-include ui/dist into jentic_one/static "
            "(run `make build-wheel`, which builds the UI first)."
        )
    print(f"OK  packaged bundle found at {static_root}")

    # Favicon / manifest / social assets (issue #614) must ride into the wheel
    # via the same force-include as the rest of the bundle (they live in
    # ui/public/ -> dist/). A missing one means the strip plugin or packaging
    # regressed and the SPA would 404 its own icons in production.
    icon_assets = (
        "favicon.svg",
        "favicon.ico",
        "favicon-96x96.png",
        "apple-touch-icon.png",
        "web-app-manifest-192x192.png",
        "web-app-manifest-512x512.png",
        "icon-512-maskable.png",
        "og-image.png",
        "site.webmanifest",
    )
    missing = [name for name in icon_assets if not (static_root / name).is_file()]
    if missing:
        raise AssertionError(
            f"icon/manifest assets missing from the packaged bundle: {missing}. "
            "They must flow from ui/public/ into ui/dist/ and force-include into "
            "the wheel (and must not be stripped like mockServiceWorker.js)."
        )
    # The dev-only MSW mock must NOT ship in the production wheel.
    if (static_root / "mockServiceWorker.js").is_file():
        raise AssertionError(
            "mockServiceWorker.js leaked into the production wheel "
            "(dropMockServiceWorker should strip it at build time)."
        )
    print(f"OK  {len(icon_assets)} icon/manifest assets packaged; MSW mock stripped")


def _check_serving() -> None:
    app = FastAPI()
    router = APIRouter()

    @router.get("/users")
    def _users() -> dict[str, bool]:
        return {"ok": True}

    app.include_router(router)
    # The agent-discovery routes read wheel-shipped package data at request
    # time, so serving one here pins the packaging of the skill references
    # (content/<name>/references/*) against the *installed* wheel (#1336).
    app.include_router(get_agent_discovery_router())

    if mount_spa(app, health_path="/admin/health") is not True:
        raise AssertionError("mount_spa returned False despite a packaged bundle")

    # No `with` block => lifespan never runs => no database connection needed.
    client = TestClient(app, raise_server_exceptions=False)

    if client.get("/users").status_code != 200:
        raise AssertionError("real API route /users did not return 200")

    # A browser navigation sends an HTML-accepting request; that is what the
    # SPA fallback keys off of.
    html_headers = {"Accept": "text/html"}

    # The SPA lives under /app, not the site root. A human typing the bare host
    # gets a 307 redirect into the app.
    root = client.get("/", headers=html_headers, follow_redirects=False)
    if root.status_code != 307 or root.headers.get("location") != f"{SPA_MOUNT_PATH}/":
        raise AssertionError(
            f"bare root did not 307-redirect to {SPA_MOUNT_PATH}/ "
            f"(got {root.status_code} -> {root.headers.get('location')!r})"
        )

    mount = client.get(f"{SPA_MOUNT_PATH}/", headers=html_headers)
    if mount.status_code != 200 or "<!doctype html" not in mount.text.lower():
        raise AssertionError(f"{SPA_MOUNT_PATH}/ did not serve the SPA index.html")

    deep = client.get(f"{SPA_MOUNT_PATH}/dashboard/some/deep/link", headers=html_headers)
    if deep.status_code != 200 or "<!doctype html" not in deep.text.lower():
        raise AssertionError("client-routed deep link did not fall back to index.html")

    # Anything OUTSIDE the /app namespace is unambiguously a backend call: an
    # unknown path is a true 404 for every client (no Accept-header guesswork),
    # so the SPA mount can never shadow the API.
    for accept in ("text/html", "application/json"):
        outside = client.get("/dashboard/some/deep/link", headers={"Accept": accept})
        if outside.status_code != 404 or "<!doctype" in outside.text.lower():
            raise AssertionError(
                f"unknown non-/app path was shadowed by the SPA instead of 404 (Accept: {accept})"
            )

    api404 = client.get("/users/does-not-exist", headers={"Accept": "application/json"})
    if api404.status_code != 404 or "<!doctype" in api404.text.lower():
        raise AssertionError("unknown API subpath was shadowed by the SPA instead of 404")

    config = client.get(APP_CONFIG_PATH)
    if config.status_code != 200 or config.json() != {"healthPath": "/admin/health"}:
        raise AssertionError("app-config endpoint did not report the deploy-mode health path")

    # A wheel-shipped skill reference must serve 200 (packaging pin: the wheel
    # force-includes content/jentic/references/*, read via importlib.resources).
    if client.get("/skills/jentic/references/cli.md").status_code != 200:
        raise AssertionError(
            "wheel-shipped skill reference /skills/jentic/references/cli.md did not serve 200"
        )

    # Root icon probes the browser/OS hardcodes to the site root (issue #614)
    # must 307-redirect into /app, and following the redirect must reach a real
    # packaged asset (not a 404) — this is the no-favicon-404 acceptance criterion
    # exercised against the *installed wheel*. ``-precomposed`` (legacy iOS, no
    # dedicated asset) resolves to the real apple-touch-icon.png.
    probe_targets = {
        "favicon.ico": "favicon.ico",
        "apple-touch-icon.png": "apple-touch-icon.png",
        "apple-touch-icon-precomposed.png": "apple-touch-icon.png",
    }
    for probe, target in probe_targets.items():
        redirect = client.get(f"/{probe}", headers=html_headers, follow_redirects=False)
        if redirect.status_code != 307 or redirect.headers.get("location") != (
            f"{SPA_MOUNT_PATH}/{target}"
        ):
            raise AssertionError(
                f"/{probe} did not 307-redirect to {SPA_MOUNT_PATH}/{target} "
                f"(got {redirect.status_code} -> {redirect.headers.get('location')!r})"
            )
        # Following the redirect must reach a real bundled asset, never a 404.
        followed = client.get(f"/{probe}", headers=html_headers)
        if followed.status_code != 200:
            raise AssertionError(
                f"/{probe} did not follow through to a 200 (got {followed.status_code})"
            )
    # The /app-scoped assets referenced by index.html / the manifest serve 200.
    for served in ("favicon.svg", "site.webmanifest"):
        resp = client.get(f"{SPA_MOUNT_PATH}/{served}")
        if resp.status_code != 200:
            raise AssertionError(
                f"{SPA_MOUNT_PATH}/{served} did not serve 200 (got {resp.status_code})"
            )

    print(
        "OK  bare root redirects to /app/, SPA served under /app, "
        "non-/app 404 preserved, root icon probes 307 into /app and resolve, "
        "runtime config exposed, no database required"
    )


def main() -> int:
    try:
        _check_packaged_bundle()
        _check_serving()
    except AssertionError as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    print("PASS  packaging smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
