"""One identity-verifier chain for every control-plane process shape (#1445).

A surface-local verifier drifts: a standalone surface that installs a narrower
chain than the combined app (say, HS256 JWTs and ``jak_`` keys but not opaque
``at_`` OAuth access tokens) 401s a token the combined app accepts. Both
factories therefore install
``shared.web.app_factory.make_platform_token_verifier``. These gates pin that:

- every control-plane process shape — the combined app, each standalone
  surface, and the shapes the Docker images run — ends up with the verifier
  that helper builds, both through the composition root and when a surface's
  ``create_app`` is called directly (no composition-root safety net);
- an injected container installer can replace the verifier on standalone and
  combined apps alike (no surface hook writes it afterwards); and
- no module other than the helper and the broker (its own data-plane
  validator) assigns ``app.state.verify_token``, so a new surface-local chain
  cannot drift in.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

import jentic_one.auth.web.verifier as verifier_mod
from jentic_one.__main__ import _build_app, _build_context
from jentic_one.shared.config import load_config
from jentic_one.shared.context import Context
from jentic_one.shared.web.app_factory import (
    CONTROL_PLANE_SURFACES,
    SURFACE_MODULES,
    create_combined_app,
)
from jentic_one.shared.web.container import AppContainer

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_ROOT = REPO_ROOT / "src" / "jentic_one"
DEFAULT_CONFIG = REPO_ROOT / "config" / "local-sqlite.yaml"

#: Process shapes that serve control-plane routes: the combined app, every
#: standalone surface, the split-deploy images' ``JENTIC__APPS``, and the
#: admin-less partial combined shape.
_SHAPES: list[list[str]] = [
    list(CONTROL_PLANE_SURFACES),
    *([surface] for surface in CONTROL_PLANE_SURFACES),
    ["admin", "auth"],
    ["admin", "control"],
    ["registry", "control"],
]

#: Modules allowed to assign ``app.state.verify_token``: the shared installer
#: and the broker's data-plane validator.
_VERIFIER_ASSIGNERS = frozenset(
    {
        "shared/web/app_factory.py",
        "broker/core/setup.py",
    }
)

_SURFACES_TAKING_CONTAINER = frozenset({"control", "auth"})


@pytest.fixture()
def platform_sentinel(monkeypatch: pytest.MonkeyPatch) -> object:
    """Make the platform chain's factory return a sentinel, so installs are observable."""
    sentinel = object()

    def _fake_superset(_ctx: Context) -> Any:
        return sentinel

    monkeypatch.setattr(verifier_mod, "make_superset_verifier", _fake_superset)
    return sentinel


def _create_surface(surface: str, ctx: Context, container: AppContainer | None = None) -> FastAPI:
    """Call a surface's own ``create_app`` — no ``__main__`` safety net."""
    mod = importlib.import_module(SURFACE_MODULES[surface])
    if surface in _SURFACES_TAKING_CONTAINER:
        app: FastAPI = mod.create_app(ctx, container=container)
    else:
        assert container is None, f"{surface}.create_app takes no container"
        app = mod.create_app(ctx)
    return app


@pytest.mark.arch
@pytest.mark.parametrize("apps", _SHAPES, ids=lambda apps: "+".join(apps))
def test_every_control_plane_shape_installs_the_platform_verifier(
    apps: list[str], platform_sentinel: object
) -> None:
    config = load_config(DEFAULT_CONFIG)
    app = _build_app(_build_context(config, apps), apps)

    assert app.state.verify_token is platform_sentinel, (
        f"apps={apps}: app.state.verify_token is not the platform verifier — "
        "install it through shared.web.app_factory.install_platform_token_verifier "
        "instead of a surface-local chain"
    )


@pytest.mark.arch
@pytest.mark.parametrize("surface", CONTROL_PLANE_SURFACES)
def test_surface_create_app_installs_the_platform_verifier_directly(
    surface: str, platform_sentinel: object
) -> None:
    """A surface's own factory installs the chain; the composition root is not needed.

    ``__main__._build_app`` backfills a missing verifier, which would hide a
    factory that skipped it. Building through ``create_app`` alone pins that
    ``create_surface_app`` itself installs it.
    """
    config = load_config(DEFAULT_CONFIG)
    app = _create_surface(surface, _build_context(config, [surface]))

    assert getattr(app.state, "verify_token", None) is platform_sentinel, (
        f"{surface}.create_app(ctx) did not install the platform verifier — "
        "create_surface_app must install it for every non-broker surface"
    )


def _replacing_installer(replacement: object) -> Any:
    def _install(app: FastAPI, _ctx: Context) -> None:
        app.state.verify_token = replacement

    return _install


@pytest.mark.arch
@pytest.mark.parametrize("surface", sorted(_SURFACES_TAKING_CONTAINER))
def test_injected_installer_replaces_verifier_on_standalone_surface(
    surface: str, platform_sentinel: object
) -> None:
    """An injected installer's verifier survives the surface's own hooks."""
    replacement = object()
    config = load_config(DEFAULT_CONFIG)
    ctx = _build_context(config, [surface])
    container = AppContainer(ctx=ctx, extra_installers=(_replacing_installer(replacement),))

    app = _create_surface(surface, ctx, container)

    assert app.state.verify_token is replacement


@pytest.mark.arch
def test_injected_installer_replaces_verifier_on_combined_app(
    platform_sentinel: object,
) -> None:
    replacement = object()
    apps = list(CONTROL_PLANE_SURFACES)
    config = load_config(DEFAULT_CONFIG)
    ctx = _build_context(config, apps)
    container = AppContainer(ctx=ctx, extra_installers=(_replacing_installer(replacement),))

    app = create_combined_app(ctx, apps, container=container)

    assert app.state.verify_token is replacement


@pytest.mark.arch
def test_standalone_broker_keeps_its_own_validator(platform_sentinel: object) -> None:
    """The broker's data-plane validator (JWKS, TTL caches) is deliberately separate."""
    config = load_config(DEFAULT_CONFIG)
    app = _build_app(_build_context(config, ["broker"]), ["broker"])

    assert app.state.verify_token is not platform_sentinel
    assert app.state.verify_token.__module__ == "jentic_one.broker.core.setup"


def _is_state_attr(node: ast.expr) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "state"


def _assigns_verify_token(tree: ast.AST) -> bool:
    """Whether ``tree`` writes ``<x>.state.verify_token`` (assignment or ``setattr``)."""
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "setattr"
            and len(node.args) >= 2
            and _is_state_attr(node.args[0])
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "verify_token"
        ):
            return True
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "verify_token"
                and _is_state_attr(target.value)
            ):
                return True
    return False


@pytest.mark.arch
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("app.state.verify_token = v", True),
        ("app.state.verify_token: Any = v", True),
        ("setattr(app.state, 'verify_token', v)", True),
        ("setattr(root.state, 'verify_token', v)", True),
        ("setattr(app.state, 'other', v)", False),
        ("app.state.other = v", False),
        ("v = app.state.verify_token", False),
    ],
)
def test_verify_token_writer_detection(source: str, expected: bool) -> None:
    assert _assigns_verify_token(ast.parse(source)) is expected


@pytest.mark.arch
def test_only_the_shared_installer_assigns_verify_token() -> None:
    offenders = sorted(
        rel
        for path in SRC_ROOT.rglob("*.py")
        if (rel := path.relative_to(SRC_ROOT).as_posix()) not in _VERIFIER_ASSIGNERS
        and _assigns_verify_token(ast.parse(path.read_text(encoding="utf-8")))
    )
    assert not offenders, (
        "These modules assign app.state.verify_token directly, which lets a surface "
        "drift from the platform verifier chain. Use "
        "shared.web.app_factory.install_platform_token_verifier instead:\n  "
        + "\n  ".join(offenders)
    )
