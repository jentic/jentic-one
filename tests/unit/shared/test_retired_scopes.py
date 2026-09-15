"""Retired-scope tolerance (theme-5 Phase 5b; theme 7).

Retired scopes are out of the catalogue, but stored grants still carry them:
user permission rows and agent ``actor_scope_grants`` written before the
retirement. Every validation path that rejects unknown scopes must
accept-and-ignore ``RETIRED_SCOPES`` members so a re-submit of a stored grant
never 422s (see ``PermissionService.validate_grants`` — exercised against a
real DB in ``tests/integration/admin/services/test_permission_service.py``).
Holding a retired scope grants nothing.
"""

from __future__ import annotations

from jentic_one.shared.auth.permission_catalog import ALL_PERMISSIONS, IMPLICATION_MAP
from jentic_one.shared.scopes import DEFAULT_AGENT_SCOPES, MCP_TOOL_SCOPES, RETIRED_SCOPES


def test_retired_scopes_pin_the_expected_set() -> None:
    """The retired set is exactly the theme-5 toolkit trio plus the theme-7
    access-request delegation scope — additions here are deliberate acts."""
    assert {
        "toolkits:read",
        "toolkits:write",
        "owner:toolkits:read",
        "owner:access-requests:read",
    } == RETIRED_SCOPES


def test_retired_scopes_are_out_of_the_catalogue() -> None:
    """Retired scopes are truly retired: no catalogue entry, default, or implication."""
    assert not RETIRED_SCOPES & set(ALL_PERMISSIONS)
    assert not RETIRED_SCOPES & set(DEFAULT_AGENT_SCOPES)
    assert not RETIRED_SCOPES & MCP_TOOL_SCOPES
    for implied in IMPLICATION_MAP.values():
        assert not RETIRED_SCOPES & implied
