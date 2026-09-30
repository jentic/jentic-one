"""Unit tests for the shared agent scope-ceiling predicate (pure, no I/O)."""

from __future__ import annotations

import pytest

from jentic_one.shared.auth.agent_scope_ceiling import is_agent_scope_grantable
from jentic_one.shared.auth.permission_catalog import ALL_PERMISSIONS, compute_effective
from jentic_one.shared.scopes import DEFAULT_AGENT_SCOPES


@pytest.mark.parametrize("scope", sorted(ALL_PERMISSIONS))
def test_org_admin_may_grant_every_catalogue_scope(scope: str) -> None:
    assert is_agent_scope_grantable(scope, compute_effective({"org:admin"}))


@pytest.mark.parametrize("scope", ["org:admin", "agents:write"])
def test_admin_only_scopes_never_grantable_by_non_admin_even_if_held(scope: str) -> None:
    held = compute_effective({"agents:write", "users:write"})
    assert scope == "org:admin" or scope in held
    assert not is_agent_scope_grantable(scope, held)


def test_non_admin_may_grant_held_implied_and_default_scopes_only() -> None:
    held = compute_effective({"agents:write", "users:write"})
    for scope in ("users:write", "users:read", "agents:read", *DEFAULT_AGENT_SCOPES):
        assert is_agent_scope_grantable(scope, held), scope
    for scope in ("credentials:write", "audit:read", "config:write"):
        assert not is_agent_scope_grantable(scope, held), scope
