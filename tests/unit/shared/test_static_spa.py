"""Tests for SPA static serving via FastAPI's native ``app.frontend()``.

The bundle is mounted under ``/app`` (``SPA_MOUNT_PATH``), not the site root.
That mount choice is the property these tests pin: the SPA owns ``/app`` and
``/app/*`` exclusively, so the serving layer needs no API-owned-prefix
bookkeeping and there is no content-negotiation guesswork for non-``/app``
paths — an unknown path outside ``/app`` is a plain 404 for *every* client,
HTML-accepting browser included. These tests pin:

* a deep-link refresh under ``/app/*`` serves ``index.html`` (SPA routing),
* an unknown path OUTSIDE ``/app`` 404s regardless of Accept (no shell leak),
* a real API route still works and is never shadowed,
* HEAD on an ``/app`` deep link works,
* the bare root 307-redirects to ``/app/``,
* the deploy-mode health path is exposed at ``/app-config.json``,
* API-only mode (no bundle) is a clean no-op.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

import jentic_one.shared.web.static as static_mod
from jentic_one.shared.web.static import (
    APP_CONFIG_PATH,
    SPA_MOUNT_PATH,
    _resolve_dev_static_dir,
    mount_spa,
)

# A browser navigating to a deep link sends an HTML-accepting request; that is
# what triggers the index.html fallback within /app. API clients send JSON.
_HTML_HEADERS = {"Accept": "text/html"}
_JSON_HEADERS = {"Accept": "application/json"}


def _make_static_bundle(tmp_path: Path) -> Path:
    """Create a minimal packaged-SPA layout (index.html + assets/) on disk."""
    static_dir = tmp_path / "static"
    (static_dir / "assets").mkdir(parents=True)
    (static_dir / "index.html").write_text("<!doctype html><title>spa</title>", encoding="utf-8")
    (static_dir / "assets" / "app.js").write_text("export default 1;", encoding="utf-8")
    return static_dir


def test_mount_spa_no_op_without_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force "no bundle" regardless of whether a source ui/dist exists locally.
    monkeypatch.setattr(static_mod, "_resolve_static_dir", lambda: None)
    app = FastAPI()
    assert mount_spa(app) is False
    client = TestClient(app, raise_server_exceptions=False)
    # No frontend registered: unknown paths are plain 404s, no config endpoint,
    # and no root redirect.
    assert client.get("/anything").status_code == 404
    assert client.get(APP_CONFIG_PATH).status_code == 404
    assert client.get("/", follow_redirects=False).status_code == 404


def test_spa_serves_index_for_app_deep_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    static_dir = _make_static_bundle(tmp_path)
    monkeypatch.setattr(static_mod, "_resolve_static_dir", lambda: static_dir)

    app = FastAPI()
    router = APIRouter()

    @router.get("/users")
    def _users() -> dict[str, str]:
        return {"ok": "yes"}

    app.include_router(router)
    assert mount_spa(app) is True

    client = TestClient(app, raise_server_exceptions=False)

    # A real API route still works.
    assert client.get("/users").status_code == 200
    # A client-routed deep-link navigation UNDER /app returns index.html.
    resp = client.get("/app/dashboard/some/deep/link", headers=_HTML_HEADERS)
    assert resp.status_code == 200
    assert "<title>spa</title>" in resp.text
    # The bundle's assets are served by the framework (under the /app mount).
    assert client.get("/app/assets/app.js").status_code == 200


def test_unknown_non_app_path_404s_regardless_of_accept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anything outside /app is unambiguously a backend path.

    Because the frontend group only matches under its /app mount prefix, an
    unknown path elsewhere never reaches the SPA handler — it 404s for every
    client, including an HTML-accepting browser. No Accept-header guesswork and
    no shell leak, which is the whole point of the /app mount.
    """
    static_dir = _make_static_bundle(tmp_path)
    monkeypatch.setattr(static_mod, "_resolve_static_dir", lambda: static_dir)

    app = FastAPI()
    router = APIRouter()

    @router.get("/users")
    def _users() -> dict[str, str]:
        return {}

    app.include_router(router)
    mount_spa(app)

    client = TestClient(app, raise_server_exceptions=False)
    for headers in (_JSON_HEADERS, _HTML_HEADERS, {"Accept": "*/*"}):
        # Unknown subpath under a real API prefix.
        resp = client.get("/users/does-not-exist", headers=headers)
        assert resp.status_code == 404, headers
        assert "<title>spa</title>" not in resp.text
        # Brand-new namespace the serving layer has never heard of.
        resp = client.get("/totally-unknown/thing", headers=headers)
        assert resp.status_code == 404, headers
        assert "<title>spa</title>" not in resp.text


def test_spa_serves_head_for_app_deep_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HEAD on a client-routed /app deep link must return 200, not 405.

    Browsers, CDNs, and uptime probes issue HEAD on deep links.
    """
    static_dir = _make_static_bundle(tmp_path)
    monkeypatch.setattr(static_mod, "_resolve_static_dir", lambda: static_dir)

    app = FastAPI()
    mount_spa(app)

    client = TestClient(app, raise_server_exceptions=False)
    assert client.head("/app/dashboard/deep/link", headers=_HTML_HEADERS).status_code == 200


def test_root_redirects_to_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The bare root is not an SPA route; it 307-redirects into the app."""
    static_dir = _make_static_bundle(tmp_path)
    monkeypatch.setattr(static_mod, "_resolve_static_dir", lambda: static_dir)

    app = FastAPI()
    mount_spa(app)

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/", headers=_HTML_HEADERS, follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == f"{SPA_MOUNT_PATH}/"


def test_root_icon_probes_redirect_into_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Root icon probes (``/favicon.ico``, ``/apple-touch-icon*.png``) the
    browser/OS hardcodes to the site root 307-redirect into the /app mount
    (issue #614), so a fresh load produces no console 404. Each probe points at
    its real bundled target; ``-precomposed`` (legacy iOS spelling, no dedicated
    asset) resolves to ``apple-touch-icon.png`` rather than a missing file. Only
    the allow-list is redirected — unrelated root paths must 404 untouched.
    """
    static_dir = _make_static_bundle(tmp_path)
    monkeypatch.setattr(static_mod, "_resolve_static_dir", lambda: static_dir)

    app = FastAPI()
    mount_spa(app)

    client = TestClient(app, raise_server_exceptions=False)
    expected_targets = {
        "favicon.ico": "favicon.ico",
        "apple-touch-icon.png": "apple-touch-icon.png",
        # Legacy iOS spelling with no dedicated asset -> the real touch icon.
        "apple-touch-icon-precomposed.png": "apple-touch-icon.png",
    }
    for probe, target in expected_targets.items():
        location = f"{SPA_MOUNT_PATH}/{target}"
        resp = client.get(f"/{probe}", follow_redirects=False)
        assert resp.status_code == 307, probe
        assert resp.headers["location"] == location, probe
        # Browsers also HEAD these probes.
        head = client.head(f"/{probe}", follow_redirects=False)
        assert head.status_code == 307, probe
        assert head.headers["location"] == location, probe

    # The redirect is scoped to the allow-list: an unrelated root probe is NOT
    # captured by these routes (it falls through to a normal 404, never a 307).
    other = client.get("/robots.txt", follow_redirects=False)
    assert other.status_code != 307, "unrelated root probe must not be redirected"


def test_app_mount_serves_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The /app mount itself serves the SPA shell for a navigation request."""
    static_dir = _make_static_bundle(tmp_path)
    monkeypatch.setattr(static_mod, "_resolve_static_dir", lambda: static_dir)

    app = FastAPI()
    mount_spa(app)

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get(f"{SPA_MOUNT_PATH}/", headers=_HTML_HEADERS)
    assert resp.status_code == 200
    assert "<title>spa</title>" in resp.text


def test_app_config_endpoint_reports_mode_health_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SPA learns the deploy-mode health path from /app-config.json."""
    static_dir = _make_static_bundle(tmp_path)
    monkeypatch.setattr(static_mod, "_resolve_static_dir", lambda: static_dir)

    # Combined mode: admin health keeps its /admin prefix.
    combined = FastAPI()
    assert mount_spa(combined, health_path="/admin/health") is True
    combined_client = TestClient(combined, raise_server_exceptions=False)
    resp = combined_client.get(APP_CONFIG_PATH)
    assert resp.status_code == 200
    assert resp.json() == {"healthPath": "/admin/health"}

    # Standalone mode: the surface prefix is dropped, health is at /health.
    standalone = FastAPI()
    assert mount_spa(standalone, health_path="/health") is True
    standalone_client = TestClient(standalone, raise_server_exceptions=False)
    assert standalone_client.get(APP_CONFIG_PATH).json() == {"healthPath": "/health"}


def test_app_config_endpoint_is_not_shadowed_by_frontend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The config route wins over the low-priority frontend fallback.

    /app-config.json lives outside the /app mount and is a real (high-priority)
    route, so even a JSON-accepting request hits the real endpoint.
    """
    static_dir = _make_static_bundle(tmp_path)
    monkeypatch.setattr(static_mod, "_resolve_static_dir", lambda: static_dir)

    app = FastAPI()
    mount_spa(app, health_path="/health")

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get(APP_CONFIG_PATH, headers=_HTML_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == {"healthPath": "/health"}


def _make_source_checkout(tmp_path: Path, *, with_bundle: bool) -> Path:
    """Simulate a source checkout: a repo root with pyproject.toml and ui/dist."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    dist = tmp_path / "ui" / "dist"
    dist.mkdir(parents=True)
    if with_bundle:
        (dist / "index.html").write_text("<!doctype html><title>dev</title>", encoding="utf-8")
    return dist


def test_dev_fallback_resolves_built_ui_dist(tmp_path: Path) -> None:
    """Running from a source checkout resolves ui/dist when it holds a build."""
    dist = _make_source_checkout(tmp_path, with_bundle=True)
    assert _resolve_dev_static_dir(repo_root=tmp_path) == dist


def test_dev_fallback_none_when_ui_dist_unbuilt(tmp_path: Path) -> None:
    """A source checkout with an empty ui/dist (placeholder) resolves to None."""
    _make_source_checkout(tmp_path, with_bundle=False)
    assert _resolve_dev_static_dir(repo_root=tmp_path) is None


def test_dev_fallback_none_when_not_a_source_checkout(tmp_path: Path) -> None:
    """No pyproject.toml at the root (e.g. a wheel install) resolves to None."""
    dist = tmp_path / "ui" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    # No pyproject.toml written: guard must refuse to resolve an unrelated dir.
    assert _resolve_dev_static_dir(repo_root=tmp_path) is None


def test_packaged_static_takes_precedence_over_dev_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the wheel-packaged static/ is present it wins over ui/dist."""
    packaged = _make_static_bundle(tmp_path / "packaged")
    dev = _make_source_checkout(tmp_path / "src", with_bundle=True)

    class _FakePackageRoot:
        """Stands in for ``importlib.resources.files("jentic_one")``.

        ``/ "static"`` yields the real packaged static dir as a filesystem Path,
        matching what a wheel install returns.
        """

        def __truediv__(self, other: str) -> Path:
            assert other == "static"
            return packaged

    monkeypatch.setattr(importlib.resources, "files", lambda _pkg: _FakePackageRoot())
    monkeypatch.setattr(static_mod, "_resolve_dev_static_dir", lambda: dev)

    assert static_mod._resolve_static_dir() == packaged


def test_dev_fallback_used_when_no_packaged_static(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no packaged static/, the source ui/dist fallback is used."""
    dev = _make_source_checkout(tmp_path, with_bundle=True)

    class _EmptyTraversable:
        def __truediv__(self, other: str) -> Path:
            return tmp_path / "does-not-exist" / other

    monkeypatch.setattr(importlib.resources, "files", lambda _pkg: _EmptyTraversable())
    monkeypatch.setattr(static_mod, "_resolve_dev_static_dir", lambda: dev)

    assert static_mod._resolve_static_dir() == dev
