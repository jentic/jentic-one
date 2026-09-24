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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import jentic_one.auth.web.app as auth_app
from jentic_one.auth.web.app import _make_auth_verifier
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AppConfig
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from jentic_one.shared.web.app_factory import create_combined_app


def _ctx(sample_config_dict: dict[str, Any]) -> Context:
    return Context(AppConfig.model_validate(sample_config_dict))


def _fake_request() -> MagicMock:
    r = MagicMock()
    r.url.path = "/test"
    return r


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


# ---------------------------------------------------------------------------
# Scope intersection tests for the access-token verifier
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth_token_intersects_consented_with_platform_scopes(
    sample_config_dict: dict[str, Any],
) -> None:
    """Third-party token permissions = intersection of user grants and consented scopes."""
    ctx = _ctx(sample_config_dict)
    resolved = Identity(
        sub="user-1",
        actor_type=ActorType.USER,
        permissions=["capabilities:read", "agents:read"],
        active=True,
        oauth_client_id="client-abc",
    )

    with (
        patch.object(auth_app, "TokenService") as mock_ts_cls,
        patch.object(auth_app, "resolve_permissions_for_actor", new_callable=AsyncMock) as mock_rp,
        patch.object(auth_app, "ApiKeyResolver"),
    ):
        mock_ts_cls.return_value.resolve_access_token = AsyncMock(return_value=resolved)
        mock_rp.return_value = (
            ["capabilities:read", "agents:read", "agents:write"],
            [],
        )

        verify = _make_auth_verifier(ctx)
        identity = await verify("at_test_token", _fake_request())

    assert "capabilities:read" in identity.permissions
    assert "agents:read" in identity.permissions
    assert "agents:write" not in identity.permissions


@pytest.mark.asyncio
async def test_oauth_token_passes_through_oidc_scopes(
    sample_config_dict: dict[str, Any],
) -> None:
    """OIDC scopes (openid/email/profile) survive even though they're not platform permissions."""
    ctx = _ctx(sample_config_dict)
    resolved = Identity(
        sub="user-1",
        actor_type=ActorType.USER,
        permissions=["openid", "email", "profile", "capabilities:read"],
        active=True,
        oauth_client_id="client-abc",
    )

    with (
        patch.object(auth_app, "TokenService") as mock_ts_cls,
        patch.object(auth_app, "resolve_permissions_for_actor", new_callable=AsyncMock) as mock_rp,
        patch.object(auth_app, "ApiKeyResolver"),
    ):
        mock_ts_cls.return_value.resolve_access_token = AsyncMock(return_value=resolved)
        mock_rp.return_value = (["capabilities:read", "agents:write"], [])

        verify = _make_auth_verifier(ctx)
        identity = await verify("at_test_token", _fake_request())

    assert "openid" in identity.permissions
    assert "email" in identity.permissions
    assert "profile" in identity.permissions
    assert "capabilities:read" in identity.permissions
    assert "agents:write" not in identity.permissions


@pytest.mark.asyncio
async def test_agent_token_uses_row_scopes(
    sample_config_dict: dict[str, Any],
) -> None:
    """Agent tokens use the scopes from the token row, ignoring resolve_permissions_for_actor."""
    ctx = _ctx(sample_config_dict)
    resolved = Identity(
        sub="agent-1",
        actor_type=ActorType.AGENT,
        permissions=["capabilities:read", "capabilities:execute"],
        active=True,
        parent_actor_id="owner-1",
    )

    with (
        patch.object(auth_app, "TokenService") as mock_ts_cls,
        patch.object(auth_app, "resolve_permissions_for_actor", new_callable=AsyncMock) as mock_rp,
        patch.object(auth_app, "ApiKeyResolver"),
    ):
        mock_ts_cls.return_value.resolve_access_token = AsyncMock(return_value=resolved)
        mock_rp.return_value = ([], ["org:admin"])

        verify = _make_auth_verifier(ctx)
        identity = await verify("at_test_token", _fake_request())

    assert identity.permissions == ["capabilities:read", "capabilities:execute"]
    assert identity.parent_permissions == ["org:admin"]


@pytest.mark.asyncio
async def test_first_party_token_uses_full_permissions(
    sample_config_dict: dict[str, Any],
) -> None:
    """First-party user tokens (no oauth_client_id) use all resolved permissions."""
    ctx = _ctx(sample_config_dict)
    resolved = Identity(
        sub="user-1",
        actor_type=ActorType.USER,
        permissions=["capabilities:read"],
        active=True,
        oauth_client_id=None,
    )

    with (
        patch.object(auth_app, "TokenService") as mock_ts_cls,
        patch.object(auth_app, "resolve_permissions_for_actor", new_callable=AsyncMock) as mock_rp,
        patch.object(auth_app, "ApiKeyResolver"),
    ):
        mock_ts_cls.return_value.resolve_access_token = AsyncMock(return_value=resolved)
        mock_rp.return_value = (
            ["capabilities:read", "agents:write", "org:admin"],
            [],
        )

        verify = _make_auth_verifier(ctx)
        identity = await verify("at_test_token", _fake_request())

    assert identity.permissions == ["capabilities:read", "agents:write", "org:admin"]
