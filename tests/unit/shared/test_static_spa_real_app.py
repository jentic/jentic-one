"""End-to-end SPA serving against the *real* admin app factories.

Unlike ``test_static_spa.py`` (which uses a synthetic app), this boots the
actual production app factories — ``create_combined_app(..., ["admin", ...])``
and the standalone admin ``create_app`` — with a realistic packaged bundle, and
asserts the production-critical invariants of the ``/app`` mount:

* the SPA is served under ``/app`` with deep-link fallback for browser
  navigation; the bare root 307-redirects to ``/app/``,
* every *real* admin API path keeps working and is never shadowed by the shell,
* an unknown path OUTSIDE ``/app`` 404s for ANY client (HTML browser included)
  — the mount makes the namespace unambiguous, so there is no Accept-header
  guesswork and no shell leak,
* the deploy-mode health path is exposed at ``/app-config.json`` (``/health``
  standalone vs ``/admin/health`` combined),
* HEAD on an ``/app`` deep link works,
* no live database is required (the bundle layer is DB-independent).

These run without DB because ``Context`` is lazy and the app factories don't
touch the database at construction time; the lifespan (which would connect) is
never entered (no ``with client:``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import jentic_one.shared.web.static as static_mod
from jentic_one.admin.web.app import create_app as create_admin_app
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.web.app_factory import create_combined_app
from jentic_one.shared.web.static import SPA_MOUNT_PATH

HTML = {"Accept": "text/html"}
JSON = {"Accept": "application/json"}


@pytest.fixture()
def ctx(sample_config_dict: dict[str, Any]) -> Context:
    return Context(AppConfig.model_validate(sample_config_dict))


def _make_realistic_bundle(tmp_path: Path) -> Path:
    """A bundle shaped like a real Vite build: index.html + hashed assets."""
    static_dir = tmp_path / "static"
    (static_dir / "assets").mkdir(parents=True)
    (static_dir / "index.html").write_text(
        "<!doctype html><html><head><title>Jentic One</title>"
        '<script type="module" src="/app/assets/index-abc123.js"></script>'
        "</head><body><div id=root></div></body></html>",
        encoding="utf-8",
    )
    (static_dir / "assets" / "index-abc123.js").write_text("export default 1;", encoding="utf-8")
    (static_dir / "assets" / "index-abc123.css").write_text("body{}", encoding="utf-8")
    (static_dir / "favicon.ico").write_text("ico", encoding="utf-8")
    (static_dir / "apple-touch-icon.png").write_text("png", encoding="utf-8")
    # Root-level (non-``assets/``) bundle files, like the real Vite build ships
    # (broker-openapi.json, cli-reference.json, favicon.svg, site.webmanifest…).
    # A missing/renamed one must 404, never boot the shell (issue #647 rev.3 #1).
    (static_dir / "broker-openapi.json").write_text('{"openapi":"3.1.0"}', encoding="utf-8")
    (static_dir / "site.webmanifest").write_text("{}", encoding="utf-8")
    return static_dir


@pytest.fixture()
def bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    static_dir = _make_realistic_bundle(tmp_path)
    monkeypatch.setattr(static_mod, "_resolve_static_dir", lambda: static_dir)
    return static_dir


def _client(app: FastAPI) -> TestClient:
    # Never enter the lifespan: no DB connection is made.
    return TestClient(app, raise_server_exceptions=False)


# --------------------------------------------------------------------------
# Combined mode (make start-app): admin health keeps its /admin prefix.
# --------------------------------------------------------------------------


def test_combined_app_serves_spa_and_config(ctx: Context, bundle: Path) -> None:
    app = create_combined_app(ctx, ["admin", "control", "registry"])
    client = _client(app)

    # SPA shell under /app for a browser navigation.
    shell = client.get(f"{SPA_MOUNT_PATH}/", headers=HTML)
    assert shell.status_code == 200
    assert "<title>Jentic One</title>" in shell.text

    # Bare root 307-redirects into the app.
    root = client.get("/", headers=HTML, follow_redirects=False)
    assert root.status_code == 307
    assert root.headers["location"] == f"{SPA_MOUNT_PATH}/"

    # Hashed asset is served by the framework (under the /app mount).
    asset = client.get(f"{SPA_MOUNT_PATH}/assets/index-abc123.js")
    assert asset.status_code == 200
    assert "export default" in asset.text

    # Runtime config reports the COMBINED health path.
    cfg = client.get("/app-config.json")
    assert cfg.status_code == 200
    assert cfg.json() == {"healthPath": "/admin/health"}

    # The config endpoint is real JSON even for an HTML-accepting client (must
    # not be shadowed by the low-priority frontend fallback).
    assert client.get("/app-config.json", headers=HTML).json() == {"healthPath": "/admin/health"}


def test_combined_app_root_icon_probes_redirect_into_app(ctx: Context, bundle: Path) -> None:
    """Browser/OS root icon probes 307-redirect into /app (issue #614), so a
    fresh load against the real combined app produces no favicon.ico 404. The
    redirect target resolves to a real bundled asset under the mount.
    """
    app = create_combined_app(ctx, ["admin", "control", "registry"])
    client = _client(app)

    expected_targets = {
        "favicon.ico": "favicon.ico",
        "apple-touch-icon.png": "apple-touch-icon.png",
        # Legacy iOS spelling, no dedicated asset -> the real touch icon.
        "apple-touch-icon-precomposed.png": "apple-touch-icon.png",
    }
    for probe, target in expected_targets.items():
        resp = client.get(f"/{probe}", headers=HTML, follow_redirects=False)
        assert resp.status_code == 307, probe
        assert resp.headers["location"] == f"{SPA_MOUNT_PATH}/{target}", probe

    # Following each redirect (including precomposed) reaches a real bundled
    # asset (not a 404) — every root probe ends at a 200.
    for probe in expected_targets:
        assert client.get(f"/{probe}").status_code == 200, probe


def test_combined_app_real_api_routes_not_shadowed(ctx: Context, bundle: Path) -> None:
    app = create_combined_app(ctx, ["admin", "control", "registry"])
    client = _client(app)

    # Every real surface health endpoint still works and returns JSON, not HTML.
    for surface in ("admin", "control", "registry"):
        resp = client.get(f"/{surface}/health", headers=JSON)
        assert resp.status_code == 200, f"/{surface}/health"
        assert resp.json()["status"] == "ok"
        assert "<title>" not in resp.text

    # docs / openapi.json (auto-added, schema-excluded) are not shadowed.
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200


def test_combined_app_unknown_non_app_path_404s_for_any_client(ctx: Context, bundle: Path) -> None:
    """The invariant the /app mount buys: an unknown path OUTSIDE /app gets a
    404 for *any* client — including an HTML-accepting browser — never the
    shell. There is no Accept-header ambiguity to pin a matrix against, because
    such paths never reach the SPA handler at all.
    """
    app = create_combined_app(ctx, ["admin", "control", "registry"])
    client = _client(app)

    targets = (
        "/admin/this-route-does-not-exist",  # unknown subpath under a real prefix
        "/totally-unknown/thing",  # namespace the serving layer never heard of
        "/admin/nope.json",  # path with an extension
    )
    for path in targets:
        for headers in (JSON, HTML, {"Accept": "*/*"}):
            resp = client.get(path, headers=headers)
            assert resp.status_code == 404, f"{path} {headers}"
            assert "<title>" not in resp.text, f"{path} {headers}"


def test_combined_app_app_deep_link_negotiation(ctx: Context, bundle: Path) -> None:
    """Within /app, ``fallback="auto"`` still governs deep-link behaviour.

    This is the one place content negotiation matters now: an unknown path under
    the /app mount. A browser navigation (HTML, extension-less) gets the SPA
    shell so deep-link refreshes work; a path with a file extension is
    non-navigation and 404s.
    """
    app = create_combined_app(ctx, ["admin"])
    client = _client(app)

    def _is_shell(resp: object) -> bool:
        return "<title>Jentic One</title>" in resp.text  # type: ignore[attr-defined]

    # Browser deep-link navigation under /app -> SPA shell (deep-link support).
    r = client.get(f"{SPA_MOUNT_PATH}/agents/some-id/settings", headers=HTML)
    assert r.status_code == 200 and _is_shell(r)

    # A path with a file extension under /app is non-navigation -> 404.
    r = client.get(f"{SPA_MOUNT_PATH}/nope.json", headers={"Accept": "*/*"})
    assert r.status_code == 404 and not _is_shell(r)


def test_combined_app_deep_link_head(ctx: Context, bundle: Path) -> None:
    app = create_combined_app(ctx, ["admin"])
    client = _client(app)

    # HEAD on an /app deep link must work (browsers/CDNs/probes issue HEAD).
    head = client.head(f"{SPA_MOUNT_PATH}/agents/some-id/settings", headers=HTML)
    assert head.status_code == 200


def test_combined_app_versioned_deep_link_serves_shell(ctx: Context, bundle: Path) -> None:
    """Issue #647: a dotted final segment (an API version) must still boot the SPA.

    FastAPI's ``fallback="auto"`` treats a final segment with a "file extension"
    as a static-file request, so a versioned deep-link like
    ``/app/workspace/<vendor>/<name>/1.0`` (final segment ``1.0``) 404s for a
    browser navigation. The dedicated HTML-navigation fallback closes that gap
    while keeping real assets and non-navigation requests 404-ing.
    """
    app = create_combined_app(ctx, ["admin"])
    client = _client(app)

    def _is_shell(resp: object) -> bool:
        return "<title>Jentic One</title>" in resp.text  # type: ignore[attr-defined]

    # The literal URL from the issue: browser navigation to a versioned API page.
    deep = f"{SPA_MOUNT_PATH}/workspace/posthog-com/posthog-com-posthog-api/1.0"
    r = client.get(deep, headers=HTML)
    assert r.status_code == 200 and _is_shell(r)
    # HEAD on the versioned deep-link works too.
    assert client.head(deep, headers=HTML).status_code == 200
    # Multi-dot versions (e.g. 2.1.3) resolve the same way.
    r = client.get(
        f"{SPA_MOUNT_PATH}/workspace/posthog-com/posthog-com-posthog-api/2.1.3", headers=HTML
    )
    assert r.status_code == 200 and _is_shell(r)

    # Guard rails — the fallback must not leak the shell for real/non-navigation:
    # a real hashed asset is still served as the asset, not the shell.
    r = client.get(f"{SPA_MOUNT_PATH}/assets/index-abc123.js")
    assert r.status_code == 200 and not _is_shell(r)
    # a MISSING asset under the hashed namespace 404s (never the shell), even for
    # a browser navigation.
    r = client.get(f"{SPA_MOUNT_PATH}/assets/missing-xyz.js", headers=HTML)
    assert r.status_code == 404 and not _is_shell(r)
    # a non-navigation client (JSON) hitting the versioned deep-link still 404s.
    r = client.get(deep, headers=JSON)
    assert r.status_code == 404 and not _is_shell(r)
    # a dotted path with a wildcard-only Accept (curl/XHR, not a real browser
    # navigation) 404s — dotted segments demand an explicit text/html accept.
    r = client.get(f"{SPA_MOUNT_PATH}/nope.json", headers={"Accept": "*/*"})
    assert r.status_code == 404 and not _is_shell(r)

    # rev.3 #1/#2: a MISSING *root-level* bundle asset (the real build ships
    # broker-openapi.json, favicon.svg, site.webmanifest… at the mount root, not
    # under assets/) must 404 even for a top-level text/html navigation — a
    # broken deploy that drops it must surface as an error, not a silent shell.
    # The decision keys off the recognized file extension (.json/.svg/.png…),
    # not a hardcoded prefix list, so root-level assets are covered.
    r = client.get(f"{SPA_MOUNT_PATH}/broker-openapi-MISSING.json", headers=HTML)
    assert r.status_code == 404 and not _is_shell(r)
    r = client.get(f"{SPA_MOUNT_PATH}/favicon-MISSING.svg", headers=HTML)
    assert r.status_code == 404 and not _is_shell(r)
    # and a real root-level asset is still served as itself (never the shell).
    r = client.get(f"{SPA_MOUNT_PATH}/broker-openapi.json")
    assert r.status_code == 200 and not _is_shell(r)


def test_combined_app_asset_serving_keeps_framework_refinements(
    ctx: Context, bundle: Path
) -> None:
    """The #647 fallback must not regress the framework's asset serving.

    The navigation fallback is an *innermost* shim (see
    :func:`mount_spa` — appended to ``user_middleware`` so it sits inside
    request-id/telemetry) that only reinterprets a 404 for a browser navigation;
    it must never front-run ``app.frontend()``'s static serving. So a real asset
    keeps its conditional-revalidation (``304``) and ``Range`` (``206``)
    behaviour, and a missing asset is a clean ``404`` (not a ``500`` from a
    hand-rolled re-stat).
    """
    app = create_combined_app(ctx, ["admin"])
    client = _client(app)
    asset = f"{SPA_MOUNT_PATH}/assets/index-abc123.js"

    # Baseline: the asset is served with a validator.
    first = client.get(asset)
    assert first.status_code == 200
    etag = first.headers.get("etag")
    assert etag, "asset response should carry an ETag validator"

    # Conditional revalidation returns 304 with an empty body (not a full 200).
    revalidated = client.get(asset, headers={"If-None-Match": etag})
    assert revalidated.status_code == 304
    assert revalidated.content == b""

    # Range requests are honoured (206 Partial Content).
    ranged = client.get(asset, headers={"Range": "bytes=0-3"})
    assert ranged.status_code == 206
    assert ranged.headers.get("content-range", "").startswith("bytes 0-3/")

    # A missing asset is a clean 404 (framework behaviour), never a 500.
    missing = client.get(f"{SPA_MOUNT_PATH}/assets/missing-xyz.js")
    assert missing.status_code == 404


def test_combined_app_rescued_shell_keeps_request_id(ctx: Context, bundle: Path) -> None:
    """The rescued deep-link shell must flow through the inner middleware.

    The fallback is the *innermost* user middleware, so the shell it swaps in for
    a navigation 404 still passes back out through ``RequestIDMiddleware`` — the
    rescued boot carries ``x-request-id`` like every other response (and, by the
    same token, telemetry records the 200 the client receives, not the inner
    404). Were the fallback outermost, the rescued response would bypass those
    wrappers.
    """
    app = create_combined_app(ctx, ["admin"])
    client = _client(app)

    deep = f"{SPA_MOUNT_PATH}/workspace/posthog-com/posthog-com-posthog-api/1.0"
    rescued = client.get(deep, headers=HTML)
    assert rescued.status_code == 200
    assert "<title>Jentic One</title>" in rescued.text
    assert rescued.headers.get("x-request-id"), (
        "rescued deep-link shell must carry x-request-id (inner middleware ran)"
    )

    # Parity check: the extension-less shell (served by app.frontend) also carries
    # it — the rescued path must match, not diverge.
    extless = client.get(f"{SPA_MOUNT_PATH}/agents/some-id/settings", headers=HTML)
    assert extless.headers.get("x-request-id")


def test_combined_app_rescued_shell_ignores_range_header(ctx: Context, bundle: Path) -> None:
    """A navigation carrying ``Range`` must get the whole shell, not a 206/416.

    rev.3 #3: the rescued shell is a full ``200`` — the docstring promises
    ``200 + index.html``. A navigation that happens to carry a ``Range`` (resumed
    navigation, some prefetchers/download managers) must still boot the SPA, so
    ``Range``/``If-Range`` are stripped before serving the shell. Otherwise a
    range-aware ``FileResponse`` would answer with a partial ``206`` (or ``416``)
    shell that fails to boot.
    """
    app = create_combined_app(ctx, ["admin"])
    client = _client(app)

    deep = f"{SPA_MOUNT_PATH}/workspace/posthog-com/posthog-com-posthog-api/1.0"
    ranged = client.get(deep, headers={**HTML, "Range": "bytes=0-3"})
    assert ranged.status_code == 200, "rescued shell must ignore Range (full 200)"
    assert "<title>Jentic One</title>" in ranged.text
    # A wildly out-of-range request must not 416 the shell either.
    huge = client.get(deep, headers={**HTML, "Range": "bytes=99999-100000"})
    assert huge.status_code == 200
    assert "<title>Jentic One</title>" in huge.text


def test_combined_app_split_accept_header_still_boots_shell(
    ctx: Context, bundle: Path
) -> None:
    """Duplicate ``Accept`` header lines must be RFC 7230-combined, not overwritten.

    A client/intermediary may split ``Accept`` across two header lines. If we
    kept only the last, a ``text/html``-first navigation would lose its HTML
    accept and 404 on a dotted deep-link (``require_explicit_html``). We combine
    all ``accept`` lines, so a real navigation still boots the shell regardless of
    line order.
    """
    app = create_combined_app(ctx, ["admin"])
    client = _client(app)
    deep = f"{SPA_MOUNT_PATH}/workspace/posthog-com/posthog-com-posthog-api/1.0"

    # Raw split header lines (httpx sends each tuple as its own line).
    for raw_headers in (
        [("accept", "text/html"), ("accept", "application/json")],
        [("accept", "application/json"), ("accept", "text/html")],
    ):
        r = client.get(deep, headers=raw_headers)
        assert r.status_code == 200, f"split Accept {raw_headers} should boot the shell"
        assert "<title>Jentic One</title>" in r.text


# --------------------------------------------------------------------------
# Standalone mode (make start-admin): surface prefix dropped, health at /health.
# --------------------------------------------------------------------------


def test_standalone_admin_app_serves_spa_and_config(ctx: Context, bundle: Path) -> None:
    app = create_admin_app(ctx)
    client = _client(app)

    # SPA under /app; bare root redirects there.
    assert client.get(f"{SPA_MOUNT_PATH}/", headers=HTML).status_code == 200
    assert client.get("/", headers=HTML, follow_redirects=False).status_code == 307

    # Standalone health is at /health (the surface prefix is dropped).
    assert client.get("/health", headers=JSON).status_code == 200

    # Runtime config reports the STANDALONE health path.
    cfg = client.get("/app-config.json")
    assert cfg.status_code == 200
    assert cfg.json() == {"healthPath": "/health"}


def test_standalone_admin_unknown_non_app_path_404s(ctx: Context, bundle: Path) -> None:
    app = create_admin_app(ctx)
    client = _client(app)

    resp = client.get("/users/does-not-exist-id-zzz/sub/path", headers=JSON)
    assert resp.status_code == 404
    assert "<title>" not in resp.text


# --------------------------------------------------------------------------
# API-only mode: no bundle -> clean no-op (no SPA, no config endpoint, no crash).
# --------------------------------------------------------------------------


def test_combined_app_api_only_when_no_bundle(
    ctx: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(static_mod, "_resolve_static_dir", lambda: None)
    app = create_combined_app(ctx, ["admin", "control", "registry"])
    client = _client(app)

    # Real API still works.
    assert client.get("/admin/health").status_code == 200
    # No SPA shell, no config endpoint, no root redirect.
    assert client.get("/app-config.json").status_code == 404
    assert client.get("/", headers=HTML, follow_redirects=False).status_code == 404
    # An unknown /app deep link is a plain 404, not an HTML shell.
    deep = client.get(f"{SPA_MOUNT_PATH}/x", headers=HTML)
    assert deep.status_code == 404
    assert "<title>" not in deep.text
