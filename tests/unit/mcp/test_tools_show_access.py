"""show_access on the mount — the Go handler's arms, replayed against the port.

Mirrors ``cli/internal/cli/api/mcp_access.go``: the caller reads its own
binding (the agent id is the identity's, never an argument), the payload is
the route's wire shape, and a credential the agent is not bound to is
RESOLVE_FAILED pointing at whoami.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest
from mcp.shared.exceptions import MCPError

import jentic_one.mcp.tools as tools_mod
from jentic_one.control.core.schema.agent_permission_rules import AgentPermissionRule
from jentic_one.control.services.credentials.errors import CredentialNotFoundError
from jentic_one.control.services.credentials.service import BindingAccess
from jentic_one.mcp.envelopes import SCHEMA_VERSION
from jentic_one.mcp.tools import CallEnv, dispatch_tool_call
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AuthConfig, ServerConfig
from jentic_one.shared.models import ActorType

_RULE = AgentPermissionRule(
    id="apr_1",
    agent_id="agnt_1",
    credential_id="cred_1",
    sequence=0,
    effect="allow",
    methods=["GET"],
    path="/repos",
    match_mode="prefix",
    operations=None,
    is_system=False,
    comment=None,
)


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


class _FakeCredentialService:
    """CredentialService stand-in: records get_binding_access calls (or raises)."""

    calls: ClassVar[list[tuple[str, str]]] = []
    error: ClassVar[Exception | None] = None

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    async def get_binding_access(
        self, credential_id: str, agent_id: str, *, identity: Identity
    ) -> BindingAccess:
        _FakeCredentialService.calls.append((credential_id, agent_id))
        if _FakeCredentialService.error is not None:
            raise _FakeCredentialService.error
        return BindingAccess(
            rules=[_RULE],
            rule_set=None,
            rule_set_rules=[],
            granted_scopes=["repo"],
            suspended=False,
        )


@pytest.fixture(autouse=True)
def service(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeCredentialService.calls = []
    _FakeCredentialService.error = None
    monkeypatch.setattr(tools_mod, "CredentialService", _FakeCredentialService)


async def test_reads_the_callers_own_binding() -> None:
    result = await dispatch_tool_call(
        _env(["owner:credentials:read"]), "show_access", {"credential_id": "cred_1"}
    )
    assert not result.is_error
    assert _FakeCredentialService.calls == [("cred_1", "agnt_1")]
    payload = _payload(result)
    assert payload["data"] == [
        {
            "id": "apr_1",
            "effect": "allow",
            "methods": ["GET"],
            "path": "/repos",
            "match_mode": "prefix",
            "operations": None,
            "_system": False,
            "_comment": None,
        }
    ]
    assert payload["rule_set"] is None
    assert payload["granted_scopes"] == ["repo"]
    assert payload["suspended"] is False
    assert payload["schema_version"] == SCHEMA_VERSION


async def test_unbound_credential_points_at_whoami() -> None:
    _FakeCredentialService.error = CredentialNotFoundError("cred_other")
    result = await dispatch_tool_call(
        _env(["owner:credentials:read"]), "show_access", {"credential_id": "cred_other"}
    )
    assert result.is_error
    payload = _payload(result)
    assert payload["error_code"] == "RESOLVE_FAILED"
    assert payload["next_tool"] == "whoami"


async def test_missing_credential_id_is_invalid_params() -> None:
    with pytest.raises(MCPError):
        await tools_mod.handle_show_access(_env(["owner:credentials:read"]), {})


async def test_without_a_credentials_read_permission_is_refused() -> None:
    result = await dispatch_tool_call(_env(["apis:read"]), "show_access", {"credential_id": "c"})
    assert result.is_error
    assert _payload(result)["error_code"] == "NOT_AUTHENTICATED"
    assert _FakeCredentialService.calls == []
