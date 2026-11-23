"""request_access_change / amend_request on the mount — the Go handlers' arms, replayed.

Mirrors ``cli/internal/cli/api/mcp_access.go``: the caller's own binding is
edited (``agent_id`` is the identity's), the poll token never reaches the
result, an already-open edit points at ``amend_request``, and a refusal that
means the caller's picture of its access is wrong carries the ``check_access``
directive with ``next_tool: show_access``.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any, ClassVar
from unittest.mock import MagicMock, patch

import pytest
from mcp.shared.exceptions import MCPError

import jentic_one.mcp.access as access_mod
import jentic_one.mcp.tools as tools_mod
from jentic_one.control.services.integrations.connect_session_service import (
    AmendedSession,
    CreatedEditSession,
    SessionAmend,
)
from jentic_one.control.services.integrations.edits import EditRequest
from jentic_one.control.services.integrations.errors import (
    ConnectOpenError,
    EditOpenError,
    RevisionChangedError,
    SessionClosedError,
    TooManyRevisionsError,
    UnknownRuleError,
)
from jentic_one.mcp.tools import CallEnv, dispatch_tool_call
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AuthConfig, ServerConfig
from jentic_one.shared.models import ActorType
from jentic_one.shared.resilience import RateLimiter
from jentic_one.shared.state import MemoryStateBackend


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
        # The edit tools spend the routes' shared bucket, so the mount carries
        # one. A fresh limiter per env, as the connect tool's tests use.
        connect_limiter=RateLimiter(
            MemoryStateBackend(),
            default_rpm=60,
            burst=20,
            namespace="test_mcp_access_change",
        ),
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
    monkeypatch.setattr(access_mod, "ConnectSessionService", _FakeService)


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


def _closed_edit() -> SessionClosedError:
    """The refusal as the service raises it for an edit: pointed at the binding."""
    error = SessionClosedError("cs_e")
    error.point_at("cred_1", "agnt_1")
    return error


@pytest.mark.parametrize(
    ("error", "next_tool", "retryable", "directive"),
    [
        (RevisionChangedError("cs_e", "csr_9", 9), "amend_request", True, False),
        (_closed_edit(), "show_access", False, True),
        (TooManyRevisionsError("cs_e", 50), "", False, False),
    ],
    ids=["revision_changed", "session_closed", "too_many_revisions"],
)
async def test_a_refusal_that_no_retry_can_fix_says_so(
    error: Exception, next_tool: str, retryable: bool, directive: bool
) -> None:
    """The generic arm tells the agent to fix the request and retry.

    These three refuse a request that is fine as sent: the recovery is a
    different call, or a human, so each carries its own step and says whether
    a retry can work at all. A pointer at ``show_access`` also keeps the
    directive naming the binding to read, rather than replacing it.
    """
    _FakeService.error = error
    result = await dispatch_tool_call(
        _env(), "amend_request", {"session_id": "cs_e", "reason": "still need it"}
    )
    assert result.is_error
    payload = _payload(result)
    assert payload["error_code"] == "RESOLVE_FAILED"
    assert payload.get("next_tool", "") == next_tool
    assert payload["retryable"] is retryable
    assert "then retry" not in payload["actionable_step"]
    if directive:
        assert payload["agent_directive"]["strategy"] == "check_access"
        assert payload["agent_directive"]["parameters"]["credential_id"] == "cred_1"
    else:
        assert "agent_directive" not in payload


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


async def test_the_edit_tools_apply_the_routes_bounds() -> None:
    """The route's caps, applied to the tool's own coercion.

    Unbounded, one call could store a list of any length verbatim on a
    revision, which ``resolve_edit`` then re-emits per scope.
    """
    rule = {"effect": "allow", "methods": ["GET"], "path": "/v1/x", "match_mode": "exact"}
    cases: list[dict[str, Any]] = [
        {"credential_id": "cred_1", "rules": {"add": [rule] * 101}},
        {"credential_id": "cred_1", "rules": {"remove": [f"apr_{i}" for i in range(101)]}},
        {"credential_id": "cred_1", "scopes": {"add": [f"s{i}" for i in range(101)]}},
        {"credential_id": "cred_1", "scopes": {"set": [f"s{i}" for i in range(101)]}},
        {
            "credential_id": "cred_1",
            "scopes": {"add": ["a"], "comments": {f"s{i}": "why" for i in range(101)}},
        },
        {"credential_id": "cred_1", "scopes": {"add": ["a"], "comments": {"a": "x" * 501}}},
        # A scalar where a list belongs was an unhandled TypeError.
        {"credential_id": "cred_1", "rules": {"add": 5}},
        # ``before`` takes a rule id. A list reaches resolve_edit's set lookup
        # as an unhashable type, with the bucket already spent.
        {"credential_id": "cred_1", "rules": {"add": [{"rule": rule, "before": ["apr_1"]}]}},
        {"credential_id": "cred_1", "rules": {"add": [{"rule": rule, "before": "a" * 31}]}},
    ]
    for arguments in cases:
        result = await dispatch_tool_call(_env(), "request_access_change", arguments)
        assert result.is_error, arguments
        assert _payload(result)["error_code"] == "MISSING_ARGUMENT", arguments


async def test_a_refused_amend_costs_the_actor_nothing() -> None:
    """Every bound applies before the bucket is spent, on both edit tools.

    A call refused for its shape must not cost the actor the quota it needs
    to send the right one: `request_access_change` validates first, and its
    sibling has the same loop to close.
    """
    env = _env()
    limiter = env.connect_limiter
    assert limiter is not None
    spent: list[str] = []

    async def record(actor: str) -> Any:
        spent.append(actor)
        return await RateLimiter.acquire(limiter, actor)

    cases: list[dict[str, Any]] = [
        {"session_id": "cs_1", "requested_scopes": [f"s{i}" for i in range(101)]},
        {
            "session_id": "cs_1",
            "requested_permission_rules": [{"effect": "allow", "path": "/x", "methods": 5}],
        },
    ]
    with patch.object(limiter, "acquire", new=record):
        for arguments in cases:
            result = await dispatch_tool_call(env, "amend_request", arguments)
            assert result.is_error, arguments
            assert _payload(result)["error_code"] == "MISSING_ARGUMENT", arguments
    assert spent == []
    assert _FakeService.amends == []


async def test_the_edit_tools_spend_the_shared_connect_bucket() -> None:
    """An actor cannot double its rate by splitting calls across HTTP and MCP."""
    env = _env()
    limiter = env.connect_limiter
    assert limiter is not None
    spent = []

    async def record(actor: str) -> Any:
        spent.append(actor)
        return await RateLimiter.acquire(limiter, actor)

    with patch.object(limiter, "acquire", new=record):
        for tool, arguments in (
            ("request_access_change", {"credential_id": "cred_1", "resume": True}),
            ("amend_request", {"session_id": "cs_1", "reason": "why"}),
        ):
            with contextlib.suppress(Exception):
                await dispatch_tool_call(env, tool, arguments)
    assert spent == ["agnt_1", "agnt_1"]


async def test_an_open_connect_for_the_same_target_is_coded_not_internal() -> None:
    """The edit tools' refusal list carries this one for a reason.

    An agent whose vendor connect is still open asked for a scope change on
    the same target; absent from the list, the refusal reached the client as
    an internal error, which says nothing it can act on. The point of the
    entry is that it comes back coded, so it is driven through the handler
    rather than the connect tool that already covers it.
    """
    _FakeService.error = ConnectOpenError("cs_open", "https://one.example.com/app/agents")
    result = await dispatch_tool_call(
        _env(), "request_access_change", {"credential_id": "cred_1", "scopes": {"add": ["repo"]}}
    )
    assert result.is_error
    payload = _payload(result)
    assert payload["error_code"] == "RESOLVE_FAILED"
    assert "cs_open" in payload["error"]
    assert _FakeService.edits == []
