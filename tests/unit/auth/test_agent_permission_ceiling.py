"""Unit tests for the agent permission ceiling (pure policy, no I/O)."""

from __future__ import annotations

import pytest

from jentic_one.auth.services.agent_permission_ceiling import check_agent_permission_grant
from jentic_one.auth.services.errors import (
    AgentPermissionExceedsOwnerError,
    PermissionNotGrantableError,
    UnknownPermissionError,
)
from jentic_one.shared.auth.agent_permission_ceiling import cap_agent_permissions
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.auth.permission_catalog import (
    ALL_PERMISSIONS,
    DEFAULT_AGENT_PERMISSIONS,
    RETIRED_PERMISSIONS,
    compute_effective,
)

_OWNER_ARCHETYPES: dict[str, set[str] | None] = {
    "no-grants": set(),
    "default-only": compute_effective(set(DEFAULT_AGENT_PERMISSIONS)),
    "member-users-write": compute_effective({"users:write"}),
    "member-agents-write": compute_effective({"agents:write"}),
    "org-admin": compute_effective({"org:admin"}),
    "ownerless": None,
}


def _identity(*permissions: str) -> Identity:
    return Identity(sub="usr_caller", permissions=list(permissions))


@pytest.mark.parametrize("permission", ["org:admin", "agents:write"])
def test_non_admin_cannot_grant_admin_only_permissions_even_if_held(permission: str) -> None:
    caller = _identity("agents:write", "agents:read")
    with pytest.raises(PermissionNotGrantableError):
        check_agent_permission_grant([permission], identity=caller)


def test_non_admin_cannot_grant_permission_it_does_not_hold() -> None:
    caller = _identity("agents:write")
    with pytest.raises(PermissionNotGrantableError):
        check_agent_permission_grant(["users:write"], identity=caller)


def test_non_admin_can_grant_held_and_implied_permissions() -> None:
    # users:write implies users:read — the ceiling is the *effective* set.
    caller = _identity("agents:write", "users:write")
    check_agent_permission_grant(["users:write", "users:read", "agents:read"], identity=caller)


def test_non_admin_can_grant_default_agent_baseline() -> None:
    caller = _identity("agents:write")
    check_agent_permission_grant(list(DEFAULT_AGENT_PERMISSIONS), identity=caller)


def test_unknown_permission_rejected_for_everyone() -> None:
    with pytest.raises(UnknownPermissionError):
        check_agent_permission_grant(["not-a:permission"], identity=_identity("org:admin"))
    with pytest.raises(UnknownPermissionError):
        check_agent_permission_grant(["not-a:permission"], identity=_identity("agents:write"))


def test_retired_permission_accepted_and_ignored() -> None:
    check_agent_permission_grant(["toolkits:read"], identity=_identity("agents:write"))
    check_agent_permission_grant(
        ["toolkits:read"], identity=_identity("org:admin"), owner_effective=set()
    )


def test_org_admin_can_grant_anything_in_catalogue() -> None:
    check_agent_permission_grant(
        ["org:admin", "agents:write", "users:write"], identity=_identity("org:admin")
    )


def test_already_held_permissions_may_be_kept() -> None:
    caller = _identity("agents:write")
    check_agent_permission_grant(
        ["org:admin", "capabilities:read"], identity=caller, already_held=["org:admin"]
    )
    with pytest.raises(PermissionNotGrantableError):
        check_agent_permission_grant(
            ["org:admin", "agents:write"], identity=caller, already_held=["org:admin"]
        )


def test_already_held_permissions_exempt_from_owner_ceiling() -> None:
    admin = _identity("org:admin")
    check_agent_permission_grant(
        ["org:admin", "capabilities:read"],
        identity=admin,
        already_held=["org:admin"],
        owner_effective=set(),
    )
    with pytest.raises(AgentPermissionExceedsOwnerError) as exc_info:
        check_agent_permission_grant(
            ["org:admin", "agents:write"],
            identity=admin,
            already_held=["org:admin"],
            owner_effective=set(),
        )
    assert exc_info.value.permission == "agents:write"


@pytest.mark.parametrize("permission", ["org:admin", "agents:write", "users:write"])
def test_admin_cannot_grant_permission_above_non_admin_owner_ceiling(permission: str) -> None:
    admin = _identity("org:admin")
    owner_effective = compute_effective({"agents:write"})
    with pytest.raises(AgentPermissionExceedsOwnerError) as exc_info:
        check_agent_permission_grant([permission], identity=admin, owner_effective=owner_effective)
    assert exc_info.value.permission == permission


def test_caller_ceiling_checked_before_owner_ceiling() -> None:
    caller = _identity("agents:write")
    with pytest.raises(PermissionNotGrantableError):
        check_agent_permission_grant(["users:write"], identity=caller, owner_effective=set())


@pytest.mark.parametrize(
    ("archetype_name", "owner_effective"),
    list(_OWNER_ARCHETYPES.items()),
    ids=list(_OWNER_ARCHETYPES.keys()),
)
@pytest.mark.parametrize(
    "permission",
    [*sorted(ALL_PERMISSIONS), next(iter(sorted(RETIRED_PERMISSIONS)))],
)
def test_grant_owner_ceiling_matches_resolution_cap(
    archetype_name: str,
    owner_effective: set[str] | None,
    permission: str,
) -> None:
    """Grant-time owner check accepts a permission iff resolution-time cap keeps it
    (or the permission is retired and ignored)."""
    _ = archetype_name
    admin = _identity("org:admin")
    if permission in RETIRED_PERMISSIONS:
        check_agent_permission_grant([permission], identity=admin, owner_effective=owner_effective)
        return

    kept = permission in set(cap_agent_permissions([permission], owner_effective))
    if kept:
        check_agent_permission_grant([permission], identity=admin, owner_effective=owner_effective)
    else:
        with pytest.raises(AgentPermissionExceedsOwnerError) as exc_info:
            check_agent_permission_grant(
                [permission], identity=admin, owner_effective=owner_effective
            )
        assert exc_info.value.permission == permission
