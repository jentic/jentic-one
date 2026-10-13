"""Pin ``6ae76df5ece5``'s copied constants and its warning line.

The migration copies the retired scope and its successor instead of importing
the permission catalogue (migrations must stay runnable against the historical
code state). These tests keep the copies honest and pin the operator-facing
warning's shape.
"""

from __future__ import annotations

import importlib

from jentic_one.shared.auth.permission_catalog import (
    CREDENTIALS_CONNECT,
    DEFAULT_AGENT_PERMISSIONS,
    MCP_TOOL_SCOPES,
    RETIRED_PERMISSIONS,
)

_migration = importlib.import_module(
    "jentic_one.migrations.admin.versions.6ae76df5ece5_map_retired_access_request_scope"
)


def test_copied_constants_match_the_catalogue() -> None:
    assert _migration.RETIRED in RETIRED_PERMISSIONS
    assert _migration.SUCCESSOR == CREDENTIALS_CONNECT
    assert _migration.SUCCESSOR in DEFAULT_AGENT_PERMISSIONS
    assert _migration.SUCCESSOR in MCP_TOOL_SCOPES


def test_mapping_replaces_in_place_and_never_duplicates() -> None:
    retired, successor = _migration.RETIRED, _migration.SUCCESSOR
    assert _migration._mapped(["a", retired, "b"]) == ["a", successor, "b"]
    assert _migration._mapped([successor, "a", retired]) == [successor, "a"]
    assert _migration._mapped(["a", retired, successor]) == ["a", successor]


def test_warning_names_the_counts_and_caps_the_id_lists() -> None:
    agents = [f"agnt_{i:02d}" for i in range(25)]
    line = _migration.warning_line(agents, ["oc_claude"], 3)
    assert line.startswith(_migration.WARNING_PREFIX)
    assert "to 25 agent(s)" in line
    assert "1 OAuth client(s) and 3 consent grant(s)" in line
    assert "agnt_19" in line
    assert "agnt_20" not in line
    assert "(+5 more)" in line
    assert "OAuth clients: oc_claude." in line
