"""Retired-scope tolerance (theme-5 Phase 5b; theme 7; theme-8 Phase 2).

Retired scopes are out of the catalogue, but stored grants still carry them:
user permission rows and agent ``actor_scope_grants`` written before the
retirement. Every validation path that rejects unknown scopes must
accept-and-ignore ``RETIRED_SCOPES`` members so a re-submit of a stored grant
never 422s (see ``PermissionService.validate_grants`` — exercised against a
real DB in ``tests/integration/admin/services/test_permission_service.py``).
Holding a retired scope grants nothing.
"""

from __future__ import annotations

from jentic_one.control.repos.service_account_migration_repo import THEME8_RETIRED_SCOPES
from jentic_one.shared.auth.permission_catalog import ALL_PERMISSIONS, IMPLICATION_MAP
from jentic_one.shared.scopes import DEFAULT_AGENT_SCOPES, MCP_TOOL_SCOPES, RETIRED_SCOPES


def test_retired_scopes_pin_the_expected_set() -> None:
    """The retired set is exactly the theme-5 toolkit trio, the theme-7
    access-request delegation scope, and the theme-8 service-account trio —
    additions here are deliberate acts."""
    assert {
        "toolkits:read",
        "toolkits:write",
        "owner:toolkits:read",
        "owner:access-requests:read",
        "service-accounts:read",
        "service-accounts:write",
        "owner:service-accounts:read",
    } == RETIRED_SCOPES


def test_retired_scopes_are_out_of_the_catalogue() -> None:
    """Retired scopes are truly retired: no catalogue entry, default, or implication."""
    assert not RETIRED_SCOPES & set(ALL_PERMISSIONS)
    assert not RETIRED_SCOPES & set(DEFAULT_AGENT_SCOPES)
    assert not RETIRED_SCOPES & MCP_TOOL_SCOPES
    for implied in IMPLICATION_MAP.values():
        assert not RETIRED_SCOPES & implied


def test_sa_migration_job_retired_set_is_retired() -> None:
    """The SA→agent migration job's frozen "not carried" set is exactly the
    theme-8 trio, and every member is a platform-retired scope (E2)."""
    assert THEME8_RETIRED_SCOPES <= RETIRED_SCOPES
    assert {s for s in RETIRED_SCOPES if "service-accounts" in s} == THEME8_RETIRED_SCOPES
