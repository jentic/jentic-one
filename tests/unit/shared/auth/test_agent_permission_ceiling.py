"""Unit tests for the shared agent permission-ceiling predicate (pure, no I/O)."""

from __future__ import annotations

import pytest

from jentic_one.shared.auth.agent_permission_ceiling import (
    cap_agent_permissions,
    is_agent_permission_grantable,
)
from jentic_one.shared.auth.permission_catalog import (
    ALL_PERMISSIONS,
    DEFAULT_AGENT_PERMISSIONS,
    compute_effective,
)


@pytest.mark.parametrize("permission", sorted(ALL_PERMISSIONS))
def test_org_admin_may_grant_every_catalogue_permission(permission: str) -> None:
    assert is_agent_permission_grantable(permission, compute_effective({"org:admin"}))


@pytest.mark.parametrize("permission", ["org:admin", "agents:write"])
def test_admin_only_permissions_never_grantable_by_non_admin_even_if_held(permission: str) -> None:
    held = compute_effective({"agents:write", "users:write"})
    assert permission == "org:admin" or permission in held
    assert not is_agent_permission_grantable(permission, held)


def test_non_admin_may_grant_held_implied_and_default_permissions_only() -> None:
    held = compute_effective({"agents:write", "users:write"})
    for permission in ("users:write", "users:read", "agents:read", *DEFAULT_AGENT_PERMISSIONS):
        assert is_agent_permission_grantable(permission, held), permission
    for permission in ("credentials:write", "audit:read", "config:write"):
        assert not is_agent_permission_grantable(permission, held), permission


@pytest.mark.parametrize(
    ("agent_grants", "owner_effective", "expected"),
    [
        # Owner holds org:admin: all grants kept including admin-only permissions.
        (
            ["org:admin", "agents:write", "config:write", "capabilities:execute"],
            compute_effective({"org:admin"}),
            ["org:admin", "agents:write", "config:write", "capabilities:execute"],
        ),
        # Ownerless agent: all grants kept unchanged.
        (
            ["org:admin", "agents:write", "config:write", "capabilities:execute"],
            None,
            ["org:admin", "agents:write", "config:write", "capabilities:execute"],
        ),
        # Owner missing a non-default permission: dropped, while defaults stay.
        (
            ["config:write", "users:write", "capabilities:execute", "capabilities:read"],
            compute_effective({"users:write"}),
            ["users:write", "capabilities:execute", "capabilities:read"],
        ),
        # Implied permissions (users:write implies users:read) survive.
        (
            ["users:read", "config:read"],
            compute_effective({"users:write"}),
            ["users:read"],
        ),
        # Default agent permissions stay even when the owner holds no grants.
        (
            [*DEFAULT_AGENT_PERMISSIONS, "config:write"],
            set(),
            list(DEFAULT_AGENT_PERMISSIONS),
        ),
        # Admin-only permissions (org:admin, agents:write) dropped for non-admin owner,
        # even if the owner holds agents:write, while implied agents:read is kept.
        (
            ["org:admin", "agents:write", "agents:read", "capabilities:execute"],
            compute_effective({"agents:write"}),
            ["agents:read", "capabilities:execute"],
        ),
    ],
    ids=[
        "owner-org-admin",
        "ownerless",
        "owner-missing-permission",
        "implied-permission-kept",
        "default-permissions-kept",
        "admin-only-dropped-for-non-admin",
    ],
)
def test_cap_agent_permissions(
    agent_grants: list[str],
    owner_effective: set[str] | None,
    expected: list[str],
) -> None:
    assert cap_agent_permissions(agent_grants, owner_effective) == expected
