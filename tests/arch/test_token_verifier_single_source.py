"""One identity-verifier chain for every control-plane process shape (#1445).

A surface-local verifier drifts: a standalone surface that installs a narrower
chain than the combined app (say, HS256 JWTs and ``jak_`` keys but not opaque
``at_`` OAuth access tokens) 401s a token the combined app accepts. Both
factories therefore install
``shared.web.app_factory.make_platform_token_verifier``. These gates pin that:

- every control-plane process shape — the combined app, each standalone
  surface, and the shapes the Docker images run — ends up with the verifier
  that helper builds; and
- no module other than the helper, the auth surface (the chain's single
  source) and the broker (its own data-plane validator) assigns
  ``app.state.verify_token``, so a new surface-local chain cannot drift in.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

import jentic_one.auth.web.app as auth_app
from jentic_one.__main__ import _build_app, _build_context
from jentic_one.shared.config import load_config
from jentic_one.shared.context import Context
from jentic_one.shared.web.app_factory import CONTROL_PLANE_SURFACES

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_ROOT = REPO_ROOT / "src" / "jentic_one"
DEFAULT_CONFIG = REPO_ROOT / "config" / "local-sqlite.yaml"

#: Process shapes that serve control-plane routes: the combined app, every
#: standalone surface, and the split-deploy images' ``JENTIC__APPS``.
_SHAPES: list[list[str]] = [
    list(CONTROL_PLANE_SURFACES),
    *([surface] for surface in CONTROL_PLANE_SURFACES),
    ["admin", "auth"],
    ["admin", "control"],
]

#: Modules allowed to assign ``app.state.verify_token``: the shared installer,
#: the auth surface (the chain itself; its ``install_on_app`` is still used by
#: callers that wire a bare app), and the broker's data-plane validator.
_VERIFIER_ASSIGNERS = frozenset(
    {
        "shared/web/app_factory.py",
        "auth/web/app.py",
        "broker/core/setup.py",
    }
)


@pytest.mark.arch
@pytest.mark.parametrize("apps", _SHAPES, ids=lambda apps: "+".join(apps))
def test_every_control_plane_shape_installs_the_platform_verifier(
    apps: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = object()

    def _fake_superset(_ctx: Context) -> Any:
        return sentinel

    monkeypatch.setattr(auth_app, "make_superset_verifier", _fake_superset)
    config = load_config(DEFAULT_CONFIG)
    app = _build_app(_build_context(config, apps), apps)

    assert app.state.verify_token is sentinel, (
        f"apps={apps}: app.state.verify_token is not the platform verifier — "
        "install it through shared.web.app_factory.install_platform_token_verifier "
        "instead of a surface-local chain"
    )


@pytest.mark.arch
def test_standalone_broker_keeps_its_own_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    """The broker's data-plane validator (JWKS, TTL caches) is deliberately separate."""
    sentinel = object()
    monkeypatch.setattr(auth_app, "make_superset_verifier", lambda _ctx: sentinel)
    config = load_config(DEFAULT_CONFIG)
    app = _build_app(_build_context(config, ["broker"]), ["broker"])

    assert app.state.verify_token is not sentinel
    assert app.state.verify_token.__module__ == "jentic_one.broker.core.setup"


def _assigns_verify_token(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "verify_token"
                and isinstance(target.value, ast.Attribute)
                and target.value.attr == "state"
            ):
                return True
    return False


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
