"""Scope enforcement on the mounted tools — parity with the REST routes fronted.

§3.2 / phase-3 item 5: each tool checks the same ``required_permissions`` the
REST route it fronts declares, through the same ``compute_effective``
expansion + ``org:admin`` bypass ``get_current_identity`` applies. A scope
failure maps exactly like the Go client's wire 403 (``mcpCoded``):
NOT_AUTHENTICATED with the get_started pointer — except ``search_catalog``,
whose 403 is a missing-scope fact the agent can fix itself (BROKER_DENIED +
request_access, the Go special case).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from mcp.shared.exceptions import MCPError

from jentic_one.mcp.envelopes import ToolError
from jentic_one.mcp.tools import CallEnv, dispatch_tool_call, require_scopes
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AuthConfig, ServerConfig
from jentic_one.shared.models import ActorType


def _env(permissions: list[str]) -> CallEnv:
    ctx = MagicMock()
    ctx.config.auth = AuthConfig(canonical_base_url="https://auth.example.com")
    ctx.config.server = ServerConfig()
    ctx.instance_id = None
    return CallEnv(
        ctx=ctx,
        identity=Identity(sub="agnt_1", permissions=permissions, actor_type=ActorType.AGENT),
        credential="jak_test",
        base_url="https://auth.example.com",
        session_id=None,
    )


def _payload(result: Any) -> dict[str, Any]:
    (content,) = result.content
    decoded = json.loads(content.text)
    assert isinstance(decoded, dict)
    return decoded


def test_require_scopes_applies_the_org_admin_bypass() -> None:
    identity = Identity(sub="usr_1", permissions=["org:admin"], actor_type=ActorType.USER)
    require_scopes(identity, ["apis:read"])  # must not raise


def test_require_scopes_expands_effective_permissions() -> None:
    """The same compute_effective expansion the REST dependency applies —
    a role/bundle permission satisfies the scopes it implies."""
    identity = Identity(sub="agnt_1", permissions=["apis:read"], actor_type=ActorType.AGENT)
    require_scopes(identity, ["apis:read"])  # direct grant passes


def test_require_scopes_failure_is_the_coded_wire_403() -> None:
    identity = Identity(sub="agnt_1", permissions=[], actor_type=ActorType.AGENT)
    with pytest.raises(ToolError) as err:
        require_scopes(identity, ["apis:read"])
    assert err.value.code == "NOT_AUTHENTICATED"
    assert "apis:read" in err.value.message
    assert "get_started" in err.value.actionable


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        # The same scopes the REST routers declare on the routes fronted:
        # POST /search → apis:read, GET /inspect → apis:read,
        # GET /jobs/{id} → jobs:read.
        ("search_apis", {"query": "github issue"}),
        ("inspect_operation", {"operation_id": "op_1"}),
        ("get_execution_result", {"job_id": "job_1"}),
    ],
)
async def test_scope_failure_renders_not_authenticated(
    tool: str, arguments: dict[str, Any]
) -> None:
    result = await dispatch_tool_call(_env([]), tool, arguments)
    assert result.is_error
    payload = _payload(result)
    assert payload["error_code"] == "NOT_AUTHENTICATED"
    assert payload["next_tool"] == "get_started"


async def test_search_catalog_scope_failure_is_the_agent_fixable_special_case() -> None:
    """GET /catalog → capabilities:read; unlike the others the agent can mint
    this scope itself, so the mapping is BROKER_DENIED + request_access."""
    result = await dispatch_tool_call(_env([]), "search_catalog", {"query": "pets"})
    assert result.is_error
    payload = _payload(result)
    assert payload["error_code"] == "BROKER_DENIED"
    assert payload["next_tool"] == "request_access"
    assert "capabilities:read" in payload["error"]


async def test_unknown_tool_is_a_protocol_error() -> None:
    with pytest.raises(MCPError):
        await dispatch_tool_call(_env([]), "made_up_tool", {})
