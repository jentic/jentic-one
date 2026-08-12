"""Unit test for the deterministic combined-app identity verifier (WI-3).

When the ``auth`` surface is enabled, ``create_combined_app`` must install the
auth surface's full-taxonomy ("superset") verifier — the one that resolves API
keys + opaque ``at_`` access tokens + HS256 JWTs — regardless of the order the
surfaces appear in ``apps``. Without this the active verifier depends on surface
ordering (the admin surface ships an HS256-only, ``at_``-blind verifier).

The verifier is a closure, so identity is asserted by monkeypatching the auth
surface's factory to a sentinel and checking ``app.state.verify_token`` is it.
"""

from __future__ import annotations

from typing import Any

import pytest

import jentic_one.auth.web.app as auth_app
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.web.app_factory import create_combined_app


def _ctx(sample_config_dict: dict[str, Any]) -> Context:
    return Context(AppConfig.model_validate(sample_config_dict))


@pytest.mark.parametrize("surfaces", [["auth", "admin"], ["admin", "auth"]])
def test_combined_app_installs_auth_superset_verifier(
    sample_config_dict: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    surfaces: list[str],
) -> None:
    sentinel = object()

    def _fake_superset(_ctx: Context) -> object:
        return sentinel

    monkeypatch.setattr(auth_app, "make_superset_verifier", _fake_superset)

    app = create_combined_app(_ctx(sample_config_dict), surfaces)

    # The auth superset wins whether admin is listed before or after auth.
    assert app.state.verify_token is sentinel


def test_combined_app_without_auth_leaves_admin_verifier(
    sample_config_dict: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No auth surface → the auth superset is never installed (hardening is a no-op)."""
    sentinel = object()
    monkeypatch.setattr(auth_app, "make_superset_verifier", lambda _ctx: sentinel)

    app = create_combined_app(_ctx(sample_config_dict), ["admin"])

    assert app.state.verify_token is not sentinel
