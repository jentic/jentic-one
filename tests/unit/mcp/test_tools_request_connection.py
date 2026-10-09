"""request_connection on the mount — the Go table tests, replayed against the port.

Mirrors ``cli/internal/cli/api/mcp_request_connection.go``'s arms (validation,
the any-of scope gate, the success shape, and the four service-error mappings)
plus the arms only the in-process port has: the caller-keyed identity injection
(an agent connects for itself; a user connects an unbound credential) and the
in-process rate limiter twin of the route's.

The load-bearing invariant: the success payload NEVER carries ``poll_token`` —
the tool surface is create-only (relay approval_url → operator approves →
whoami → retry), so the capability token must not leak to a caller that has no
poll leg to spend it on.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest
from mcp.shared.exceptions import MCPError

import jentic_one.mcp.tools as tools_mod
from jentic_one.control.services.integrations.connect_session_service import (
    ApiTarget,
    CreatedSession,
)
from jentic_one.control.services.integrations.errors import (
    AuthTypeNotDeclaredError,
    AuthTypeRequiredError,
    ManualFlowsDisabledError,
    NoDeclaredSchemeError,
    NoOpForFlowError,
    RecentlyRejectedError,
    ReservedAuthFieldError,
    SecuritySchemesLookupUnavailableError,
    TooManyOpenSessionsError,
    UnknownApiError,
    UnpinnedServerHostError,
)
from jentic_one.control.services.vendors.service import (
    AmbiguousVendorError,
    UnknownVendorError,
    UnsupportedFlowError,
    VendorNotConfiguredError,
)
from jentic_one.mcp.tools import CallEnv, dispatch_tool_call
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AuthConfig, ServerConfig
from jentic_one.shared.models import ActorType
from jentic_one.shared.resilience import RateLimiter
from jentic_one.shared.state import MemoryStateBackend

_CREATED = CreatedSession(
    session_id="cs_1",
    approval_url="https://auth.example.com/connect/cs_1",
    poll_token="pt_secret",
    resolved_flow="authorization_code",
)


def _env(
    permissions: list[str],
    *,
    actor_type: ActorType = ActorType.AGENT,
    sub: str = "agnt_1",
    security_schemes_lookup: Any = None,
) -> CallEnv:
    ctx = MagicMock()
    ctx.config.auth = AuthConfig(canonical_base_url="https://auth.example.com")
    ctx.config.server = ServerConfig()
    ctx.instance_id = None
    return CallEnv(
        ctx=ctx,
        identity=Identity(sub=sub, permissions=permissions, actor_type=actor_type),
        credential="jak_test",
        base_url="https://auth.example.com",
        session_id=None,
        security_schemes_lookup=security_schemes_lookup,
    )


def _payload(result: Any) -> dict[str, Any]:
    (content,) = result.content
    decoded = json.loads(content.text)
    assert isinstance(decoded, dict)
    return decoded


class _FakeConnectSessionService:
    """ConnectSessionService stand-in: records create_session kwargs (or raises)."""

    calls: ClassVar[list[dict[str, Any]]] = []
    seams: ClassVar[list[dict[str, Any]]] = []
    error: ClassVar[Exception | None] = None
    created: ClassVar[CreatedSession] = _CREATED

    def __init__(self, ctx: Any, **seams: Any) -> None:
        self._ctx = ctx
        _FakeConnectSessionService.seams.append(seams)

    async def create_session(self, **kwargs: Any) -> CreatedSession:
        if _FakeConnectSessionService.error is not None:
            raise _FakeConnectSessionService.error
        _FakeConnectSessionService.calls.append(kwargs)
        return _FakeConnectSessionService.created


@pytest.fixture(autouse=True)
def service(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeConnectSessionService.calls = []
    _FakeConnectSessionService.seams = []
    _FakeConnectSessionService.error = None
    _FakeConnectSessionService.created = _CREATED
    monkeypatch.setattr(tools_mod, "ConnectSessionService", _FakeConnectSessionService)
    # A fresh limiter per test: the module-level one is shared process state
    # and an earlier test's spend must never bleed into this one's budget.
    monkeypatch.setattr(
        tools_mod,
        "_connect_limiter",
        RateLimiter(
            MemoryStateBackend(),
            default_rpm=30,
            burst=10,
            namespace="test_mcp_integrations_connect",
        ),
    )


# ── validation arms ──────────────────────────────────────────────────────────


async def test_missing_vendor_is_invalid_params() -> None:
    with pytest.raises(MCPError) as err:
        await tools_mod.handle_request_connection(_env(["credentials:connect"]), {})
    assert "vendor" in str(err.value)
    assert _FakeConnectSessionService.calls == []


async def test_overlong_reason_is_invalid_params() -> None:
    """The route's pydantic bound (reason max_length=1024), enforced in the
    handler because the in-process call skips the route's validation."""
    with pytest.raises(MCPError) as err:
        await tools_mod.handle_request_connection(
            _env(["credentials:connect"]), {"vendor": "github", "reason": "x" * 1025}
        )
    assert "1024" in str(err.value)
    assert _FakeConnectSessionService.calls == []


async def test_too_many_scopes_is_invalid_params() -> None:
    """Twin of the Go mount's connectScopesMax cap (100)."""
    scopes = [f"scope{i}" for i in range(101)]
    with pytest.raises(MCPError) as err:
        await tools_mod.handle_request_connection(
            _env(["credentials:connect"]), {"vendor": "github", "requested_scopes": scopes}
        )
    assert "100" in str(err.value)
    assert _FakeConnectSessionService.calls == []


# ── scope gate (the route's any-of: credentials:connect | credentials:write) ──


async def test_missing_scope_is_broker_denied_routed_to_operator() -> None:
    result = await dispatch_tool_call(_env([]), "request_connection", {"vendor": "github"})
    assert result.is_error

    payload = _payload(result)
    assert payload["error_code"] == "BROKER_DENIED"
    assert "credentials:connect" in payload["error"]
    assert "operator" in payload["actionable_step"]
    assert "next_tool" not in payload
    assert _FakeConnectSessionService.calls == []


@pytest.mark.parametrize("scope", ["credentials:connect", "credentials:write"])
async def test_either_any_of_scope_admits_the_call(scope: str) -> None:
    result = await dispatch_tool_call(_env([scope]), "request_connection", {"vendor": "github"})
    assert not result.is_error, result.content


# ── success shape ─────────────────────────────────────────────────────────────


async def test_success_returns_session_without_poll_token() -> None:
    result = await dispatch_tool_call(
        _env(["credentials:connect"]),
        "request_connection",
        {"vendor": "github", "requested_scopes": ["repo"], "reason": "read PRs"},
    )
    assert not result.is_error, result.content

    payload = _payload(result)
    assert payload["session_id"] == "cs_1"
    assert payload["approval_url"] == "https://auth.example.com/connect/cs_1"
    assert payload["resolved_flow"] == "authorization_code"
    assert "approval_url" in payload["instruction"]
    assert "whoami" in payload["instruction"]
    # THE invariant: create-only surface, no capability token to spend.
    assert "poll_token" not in payload
    assert "pt_secret" not in json.dumps(payload)

    (call,) = _FakeConnectSessionService.calls
    assert call == {
        "vendor_key": "github",
        "api_target": None,
        "auth_type": None,
        "agent_id": "agnt_1",  # the caller IS the agent — identity injected
        "initiator_actor_id": "agnt_1",
        "requested_scopes": ["repo"],
        "preferred_flow": None,
        "reason": "read PRs",
    }


async def test_scopes_alias_and_omitted_optionals_normalize() -> None:
    result = await dispatch_tool_call(
        _env(["credentials:connect"]),
        "request_connection",
        {"vendor": "github", "scopes": ["repo", "read:org"]},
    )
    assert not result.is_error, result.content
    (call,) = _FakeConnectSessionService.calls
    assert call["requested_scopes"] == ["repo", "read:org"]
    assert call["reason"] is None


async def test_explicit_agent_id_argument_is_dropped_never_forwarded() -> None:
    """Adversarial (review L4): a caller supplying ``agent_id`` in the tool
    arguments must never impersonate — the normalizer drops the unknown key
    and the handler injects the caller's own identity, exactly like the
    route's 403-on-supplied-agent_id posture (the tool surface simply has no
    such parameter to refuse)."""
    result = await dispatch_tool_call(
        _env(["credentials:connect"], sub="agnt_self"),
        "request_connection",
        {"vendor": "github", "agent_id": "agnt_other"},
    )
    assert not result.is_error, result.content

    (call,) = _FakeConnectSessionService.calls
    assert call["agent_id"] == "agnt_self"
    assert call["initiator_actor_id"] == "agnt_self"
    assert "agnt_other" not in json.dumps(call)


async def test_non_agent_caller_connects_an_unbound_credential() -> None:
    """A user over this mount connects WITHOUT an agent
    binding (the route's semantics when agent_id is omitted) — the tool
    surface carries no agent_id, so it can never bind on someone's behalf."""
    result = await dispatch_tool_call(
        _env(["credentials:write"], actor_type=ActorType.USER, sub="usr_1"),
        "request_connection",
        {"vendor": "github"},
    )
    assert not result.is_error, result.content
    (call,) = _FakeConnectSessionService.calls
    assert call["agent_id"] is None
    assert call["initiator_actor_id"] == "usr_1"


# ── service-error mapping (Go: requestConnectionError) ───────────────────────


async def test_unknown_vendor_is_resolve_failed_pointing_at_search_catalog() -> None:
    _FakeConnectSessionService.error = UnknownVendorError("nope")
    result = await dispatch_tool_call(
        _env(["credentials:connect"]), "request_connection", {"vendor": "nope"}
    )
    assert result.is_error

    payload = _payload(result)
    assert payload["error_code"] == "RESOLVE_FAILED"
    assert payload["next_tool"] == "search_catalog"
    assert "operator" in payload["actionable_step"]


@pytest.mark.parametrize(
    "error",
    [UnsupportedFlowError("github", "device_authorization"), NoOpForFlowError("saml")],
)
async def test_unusable_default_flow_is_resolve_failed_like_the_go_mount(
    error: Exception,
) -> None:
    """Cross-mount alignment (review L1): the route answers 400 for an
    unusable flow (404 for an unknown vendor), and the Go mount renders
    both as RESOLVE_FAILED + search_catalog — pin the same posture
    here so the two mounts never teach different recoveries for the same
    denial."""
    _FakeConnectSessionService.error = error
    result = await dispatch_tool_call(
        _env(["credentials:connect"]), "request_connection", {"vendor": "github"}
    )
    assert result.is_error

    payload = _payload(result)
    assert payload["error_code"] == "RESOLVE_FAILED"
    assert payload["next_tool"] == "search_catalog"
    assert "operator" in payload["actionable_step"]


async def test_ambiguous_vendor_routes_to_operator() -> None:
    """The tool carries no registration pin, so several shared OAuth apps for
    one vendor can only be disambiguated by the human in the dashboard."""
    _FakeConnectSessionService.error = AmbiguousVendorError(
        "googleapis-com", None, ["oar_a", "oar_b"]
    )
    result = await dispatch_tool_call(
        _env(["credentials:connect"]), "request_connection", {"vendor": "googleapis-com"}
    )
    assert result.is_error

    payload = _payload(result)
    assert payload["error_code"] == "RESOLVE_FAILED"
    assert "operator" in payload["actionable_step"]


async def test_vendor_not_configured_is_broker_denied_operator_action() -> None:
    _FakeConnectSessionService.error = VendorNotConfiguredError(
        "github", "authorization_code", "no client_id"
    )
    result = await dispatch_tool_call(
        _env(["credentials:connect"]), "request_connection", {"vendor": "github"}
    )
    assert result.is_error

    payload = _payload(result)
    assert payload["error_code"] == "BROKER_DENIED"
    assert "not configured" in payload["error"]
    assert "operator" in payload["actionable_step"]


# ── rate limit (the route's per-actor policy, carried in-process) ─────────────


async def test_rate_limit_is_retryable_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tools_mod,
        "_connect_limiter",
        RateLimiter(
            MemoryStateBackend(),
            default_rpm=1,
            burst=1,
            namespace="test_mcp_integrations_connect_tight",
        ),
    )
    env = _env(["credentials:connect"])
    first = await dispatch_tool_call(env, "request_connection", {"vendor": "github"})
    assert not first.is_error, first.content

    second = await dispatch_tool_call(env, "request_connection", {"vendor": "github"})
    assert second.is_error

    payload = _payload(second)
    assert payload["error_code"] == "TRANSPORT_ERROR"
    assert payload["retryable"] is True
    assert payload["retry_after_s"] >= 0
    assert len(_FakeConnectSessionService.calls) == 1


# ── registry-API targets (Go: the api / auth_type arms) ──────────────────────

_STRIPE = {"vendor": "stripe-com", "name": "stripe-com-api", "version": "2024-06-20"}


@pytest.mark.parametrize("api", [_STRIPE, "stripe-com/stripe-com-api/2024-06-20"])
async def test_api_target_forwards_identity_auth_type_and_registry_seam(api: Any) -> None:
    """``api`` (object or slug) and ``auth_type`` reach the service as the
    route passes them, and the process's registry lookup is threaded in."""
    lookup = object()
    result = await dispatch_tool_call(
        _env(["credentials:connect"], security_schemes_lookup=lookup),
        "request_connection",
        {"api": api, "auth_type": "api_key", "reason": "list charges"},
    )
    assert not result.is_error, result.content

    (call,) = _FakeConnectSessionService.calls
    assert call["vendor_key"] == ""
    assert call["api_target"] == ApiTarget(
        vendor="stripe-com", name="stripe-com-api", version="2024-06-20"
    )
    assert call["auth_type"] == "api_key"
    assert _FakeConnectSessionService.seams == [{"security_schemes_lookup": lookup}]


@pytest.mark.parametrize(
    "arguments",
    [
        {"vendor": "github", "api": _STRIPE},
        {"api": "stripe-com/stripe-com-api"},
        {"api": {"vendor": "stripe-com", "name": "stripe-com-api"}},
        {"vendor": "github", "auth_type": "api_key"},
        {"api": _STRIPE, "auth_type": "x" * 256},
    ],
)
async def test_malformed_api_target_is_invalid_params(arguments: dict[str, Any]) -> None:
    with pytest.raises(MCPError):
        await tools_mod.handle_request_connection(_env(["credentials:connect"]), arguments)
    assert _FakeConnectSessionService.calls == []


@pytest.mark.parametrize("flow", ["manual_api_key", "manual_basic", "awaiting_app"])
async def test_human_entry_flow_says_end_your_turn_and_retry_execute(flow: str) -> None:
    """A human types the credential, which can take hours, and this surface
    has no status tool: end the turn and retry execute later."""
    _FakeConnectSessionService.created = CreatedSession(
        session_id="cs_m",
        approval_url="https://auth.example.com/app/agents?approve=cs_m",
        poll_token="pt_secret",
        resolved_flow=flow,
    )
    result = await dispatch_tool_call(
        _env(["credentials:connect"]), "request_connection", {"api": _STRIPE}
    )
    assert not result.is_error, result.content

    payload = _payload(result)
    assert payload["resolved_flow"] == flow
    assert payload["next_tool"] == "execute"
    assert "end your turn" in payload["instruction"]
    assert "pt_secret" not in json.dumps(payload)


async def test_oauth_flow_keeps_the_relay_instruction_without_next_tool() -> None:
    result = await dispatch_tool_call(
        _env(["credentials:connect"]), "request_connection", {"api": _STRIPE}
    )
    payload = _payload(result)
    assert "next_tool" not in payload
    assert "end your turn" not in payload["instruction"]


_TARGET = "stripe-com/stripe-com-api/2024-06-20"


@pytest.mark.parametrize(
    ("error", "code", "next_tool", "needle"),
    [
        (ManualFlowsDisabledError(), "BROKER_DENIED", None, "Report the gap"),
        (
            UnknownApiError("stripe-com", "stripe-com-api", "2024-06-20"),
            "RESOLVE_FAILED",
            "search_catalog",
            "import_api",
        ),
        (
            AuthTypeRequiredError(["api_key", "oauth2"]),
            "RESOLVE_FAILED",
            "request_connection",
            "api_key, oauth2",
        ),
        (
            AuthTypeNotDeclaredError("digest", ["api_key"]),
            "RESOLVE_FAILED",
            "request_connection",
            "auth_type",
        ),
        (
            NoDeclaredSchemeError("stripe-com", "stripe-com-api", "2024-06-20"),
            "BROKER_DENIED",
            None,
            "Do not retry",
        ),
        (UnpinnedServerHostError(["region"]), "BROKER_DENIED", None, "enum"),
        (ReservedAuthFieldError("Host"), "BROKER_DENIED", None, "operator"),
        (SecuritySchemesLookupUnavailableError(), "BROKER_DENIED", None, "registry"),
        (TooManyOpenSessionsError("agent", 10), "BROKER_DENIED", None, "Do not open more"),
        (RecentlyRejectedError(3600), "BROKER_DENIED", None, "Do not ask again"),
    ],
)
async def test_api_target_errors_map_like_the_go_mount(
    error: Exception, code: str, next_tool: str | None, needle: str
) -> None:
    _FakeConnectSessionService.error = error
    result = await dispatch_tool_call(
        _env(["credentials:connect"]), "request_connection", {"api": _STRIPE}
    )
    assert result.is_error

    payload = _payload(result)
    assert payload["error_code"] == code
    assert payload.get("next_tool") == next_tool
    assert payload["retryable"] is False
    assert needle in payload["actionable_step"]
    if isinstance(error, AuthTypeRequiredError):
        assert payload["details"]["options"] == ["api_key", "oauth2"]
    if isinstance(error, ManualFlowsDisabledError | NoDeclaredSchemeError):
        assert _TARGET in payload["actionable_step"]
