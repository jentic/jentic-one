"""request_access_change / amend_request on the mount — the Go handlers' arms, replayed.

Mirrors ``cli/internal/cli/api/mcp_access.go``: the caller's own binding is
edited (``agent_id`` is the identity's), the poll token never reaches the
result, an already-open edit points at ``amend_request``, and a refusal that
means the caller's picture of its access is wrong carries the ``check_access``
directive with ``next_tool: show_access``.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest
from mcp.shared.exceptions import MCPError

import jentic_one.mcp.tools as tools_mod
from jentic_one.control.services.integrations.connect_session_service import (
    AmendedSession,
    CreatedEditSession,
    SessionAmend,
)
from jentic_one.control.services.integrations.edits import EditRequest
from jentic_one.control.services.integrations.errors import EditOpenError, UnknownRuleError
from jentic_one.mcp.tools import CallEnv, dispatch_tool_call
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AuthConfig, ServerConfig
from jentic_one.shared.models import ActorType


def _env() -> CallEnv:
    ctx = MagicMock()
    ctx.config.auth = AuthConfig(canonical_base_url="https://auth.example.com")
    ctx.config.server = ServerConfig()
    ctx.instance_id = None
    return CallEnv(
        ctx=ctx,
        identity=Identity(
            sub="agnt_1", permissions=["credentials:connect"], actor_type=ActorType.AGENT
        ),
        credential="jak_test",
        base_url="https://one.example.com",
        session_id=None,
    )


def _payload(result: Any) -> dict[str, Any]:
    (content,) = result.content
    decoded = json.loads(content.text)
    assert isinstance(decoded, dict)
    return decoded


class _FakeService:
    edits: ClassVar[list[dict[str, Any]]] = []
    amends: ClassVar[list[tuple[str, SessionAmend]]] = []
    error: ClassVar[Exception | None] = None

    def __init__(self, ctx: Any, **seams: Any) -> None:
        pass

    async def create_edit_session(self, **kwargs: Any) -> CreatedEditSession:
        if _FakeService.error is not None:
            raise _FakeService.error
        _FakeService.edits.append(kwargs)
        return CreatedEditSession(
            session_id="cs_e",
            approval_url="https://one.example.com/app/agents?approve=cs_e",
            poll_token="pt_secret",
            revision_id="csr_1",
        )

    async def amend_session(
        self, session_id: str, *, identity: Identity, amend: SessionAmend
    ) -> AmendedSession:
        if _FakeService.error is not None:
            raise _FakeService.error
        _FakeService.amends.append((session_id, amend))
        return AmendedSession(session_id=session_id, revision_id="csr_2", revision_number=2)


@pytest.fixture(autouse=True)
def service(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeService.edits = []
    _FakeService.amends = []
    _FakeService.error = None
    monkeypatch.setattr(tools_mod, "ConnectSessionService", _FakeService)


async def test_the_callers_own_binding_is_edited_and_the_poll_token_withheld() -> None:
    result = await dispatch_tool_call(
        _env(),
        "request_access_change",
        {
            "credential_id": "cred_1",
            "rules": {
                "add": [
                    {
                        "effect": "allow",
                        "methods": ["POST"],
                        "path": "/send",
                        "match_mode": "exact",
                    },
                    {"rule": {"effect": "deny", "path": "/admin"}, "before": "apr_1"},
                ],
                "remove": ["apr_9"],
            },
            "scopes": {"add": ["gmail.send"], "remove": ["gmail.modify"]},
            "resume": True,
        },
    )
    assert not result.is_error, result.content
    (call,) = _FakeService.edits
    assert (call["credential_id"], call["agent_id"]) == ("cred_1", "agnt_1")
    request: EditRequest = call["request"]
    assert [a.before for a in request.add] == [None, "apr_1"]
    assert (request.remove, request.add_scopes, request.resume) == (["apr_9"], ["gmail.send"], True)
    assert request.remove_scopes == ["gmail.modify"]
    payload = _payload(result)
    assert (payload["session_id"], payload["revision_id"]) == ("cs_e", "csr_1")
    assert "pt_secret" not in json.dumps(payload)


async def test_an_open_edit_points_at_amend() -> None:
    _FakeService.error = EditOpenError("cs_open")
    result = await dispatch_tool_call(
        _env(), "request_access_change", {"credential_id": "cred_1", "resume": True}
    )
    assert not result.is_error
    payload = _payload(result)
    assert (payload["session_id"], payload["next_tool"]) == ("cs_open", "amend_request")


async def test_a_wrong_picture_of_access_points_at_show_access() -> None:
    error = UnknownRuleError("apr_x")
    error.point_at("cred_1", "agnt_1")
    _FakeService.error = error
    result = await dispatch_tool_call(
        _env(), "request_access_change", {"credential_id": "cred_1", "rules": {"remove": ["apr_x"]}}
    )
    assert result.is_error
    payload = _payload(result)
    assert (payload["error_code"], payload["next_tool"]) == ("RESOLVE_FAILED", "show_access")
    directive = payload["agent_directive"]
    assert directive["strategy"] == "check_access"
    assert directive["parameters"]["access_url"] == (
        "https://one.example.com/credentials/cred_1/agents/agnt_1/permissions"
    )


async def test_set_with_add_is_invalid_params() -> None:
    with pytest.raises(MCPError):
        await tools_mod.handle_request_access_change(
            _env(),
            {
                "credential_id": "cred_1",
                "rules": {"set": [{"effect": "allow", "path": "/"}], "add": [{"effect": "deny"}]},
            },
        )


async def test_an_amend_carries_only_the_fields_sent() -> None:
    result = await dispatch_tool_call(
        _env(),
        "amend_request",
        {"session_id": "cs_e", "requested_scopes": ["repo"], "reason": "also labels"},
    )
    assert not result.is_error, result.content
    ((session_id, amend),) = _FakeService.amends
    assert session_id == "cs_e"
    assert amend.fields == frozenset({"requested_scopes", "reason"})
    assert (amend.requested_scopes, amend.reason) == (["repo"], "also labels")
    assert _payload(result)["revision_number"] == 2


async def test_a_refused_rule_comes_back_as_a_tool_error_naming_it() -> None:
    # The client may show a protocol error only as a generic failure, so the
    # reason has to ride the tool result.
    result = await dispatch_tool_call(
        _env(),
        "request_access_change",
        {
            "credential_id": "cred_1",
            "rules": {
                "add": [{"effect": "allow", "methods": ["POST"], "path": "/drafts(/[^/]+)+"}]
            },
        },
    )
    assert result.is_error
    payload = _payload(result)
    assert payload["error_code"] == "MISSING_ARGUMENT"
    assert "rules.add[0]" in payload["error"]
    assert _FakeService.edits == []
