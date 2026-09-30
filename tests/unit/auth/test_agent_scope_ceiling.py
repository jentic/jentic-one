"""Unit tests for the agent scope ceiling (pure policy, no I/O)."""

from __future__ import annotations

import pytest

from jentic_one.auth.services.agent_scope_ceiling import check_agent_scope_grant
from jentic_one.auth.services.errors import ScopeNotGrantableError, UnknownScopeError
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.scopes import DEFAULT_AGENT_SCOPES


def _identity(*permissions: str) -> Identity:
    return Identity(sub="usr_caller", permissions=list(permissions))


@pytest.mark.parametrize("scope", ["org:admin", "agents:write"])
def test_non_admin_cannot_grant_admin_only_scopes_even_if_held(scope: str) -> None:
    caller = _identity("agents:write", "agents:read")
    with pytest.raises(ScopeNotGrantableError):
        check_agent_scope_grant([scope], identity=caller)


def test_non_admin_cannot_grant_scope_it_does_not_hold() -> None:
    caller = _identity("agents:write")
    with pytest.raises(ScopeNotGrantableError):
        check_agent_scope_grant(["users:write"], identity=caller)


def test_non_admin_can_grant_held_and_implied_scopes() -> None:
    # users:write implies users:read — the ceiling is the *effective* set.
    caller = _identity("agents:write", "users:write")
    check_agent_scope_grant(["users:write", "users:read", "agents:read"], identity=caller)


def test_non_admin_can_grant_default_agent_baseline() -> None:
    caller = _identity("agents:write")
    check_agent_scope_grant(list(DEFAULT_AGENT_SCOPES), identity=caller)


def test_unknown_scope_rejected_for_everyone() -> None:
    with pytest.raises(UnknownScopeError):
        check_agent_scope_grant(["not-a:scope"], identity=_identity("org:admin"))
    with pytest.raises(UnknownScopeError):
        check_agent_scope_grant(["not-a:scope"], identity=_identity("agents:write"))


def test_retired_scope_accepted_and_ignored() -> None:
    check_agent_scope_grant(["toolkits:read"], identity=_identity("agents:write"))


def test_org_admin_can_grant_anything_in_catalogue() -> None:
    check_agent_scope_grant(
        ["org:admin", "agents:write", "users:write"], identity=_identity("org:admin")
    )


def test_already_held_scopes_may_be_kept() -> None:
    caller = _identity("agents:write")
    check_agent_scope_grant(
        ["org:admin", "capabilities:read"], identity=caller, already_held=["org:admin"]
    )
    with pytest.raises(ScopeNotGrantableError):
        check_agent_scope_grant(
            ["org:admin", "agents:write"], identity=caller, already_held=["org:admin"]
        )
