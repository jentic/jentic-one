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
from jentic_one.control.services.integrations.connect_session_service import CreatedSession
from jentic_one.control.services.integrations.errors import (
    InvalidOAuthAppRegistrationError,
    NoOpForFlowError,
)
from jentic_one.control.services.vendors.schemas import VendorEntry
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
    catalog_auto_importer: Any = None,
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
        catalog_auto_importer=catalog_auto_importer,
    )


def _payload(result: Any) -> dict[str, Any]:
    (content,) = result.content
    decoded = json.loads(content.text)
    assert isinstance(decoded, dict)
    return decoded


class _FakeConnectSessionService:
    """ConnectSessionService stand-in: records create_session kwargs (or raises)."""

    calls: ClassVar[list[dict[str, Any]]] = []
    importers: ClassVar[list[Any]] = []
    error: ClassVar[Exception | None] = None

    def __init__(self, ctx: Any, catalog_auto_importer: Any = None) -> None:
        self._ctx = ctx
        _FakeConnectSessionService.importers.append(catalog_auto_importer)

    async def create_session(self, **kwargs: Any) -> CreatedSession:
        if _FakeConnectSessionService.error is not None:
            raise _FakeConnectSessionService.error
        _FakeConnectSessionService.calls.append(kwargs)
        return _CREATED


@pytest.fixture(autouse=True)
def service(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeConnectSessionService.calls = []
    _FakeConnectSessionService.importers = []
    _FakeConnectSessionService.error = None
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
        "agent_id": "agnt_1",  # the caller IS the agent — identity injected
        "initiator_actor_id": "agnt_1",
        "requested_scopes": ["repo"],
        "requested_permission_rules": None,
        "preferred_flow": None,
        "reason": "read PRs",
        "oauth_app_registration_id": None,
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


def _vendor_entry(registration_id: str | None, key: str, name: str) -> VendorEntry:
    return VendorEntry(
        entry_id=registration_id or key,
        registration_id=registration_id,
        key=key,
        display_name="Google",
        name=name,
        flow_kind="authorization_code",
        flow_kinds=["authorization_code"],
        client_id="cid",
        source="db" if registration_id else "config",
    )


class _FakeVendorRegistryService:
    entries: ClassVar[list[VendorEntry]] = []

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    async def list_entries(self) -> list[VendorEntry]:
        return _FakeVendorRegistryService.entries


async def test_ambiguous_vendor_lists_candidates_and_asks_the_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Several shared OAuth apps serve the vendor: the error lists the ones the
    resolver counted (name + registration_id) and tells the agent to ask its
    user, then retry with the picked id — choosing the app is the user's call."""
    _FakeVendorRegistryService.entries = [
        _vendor_entry("oar_a", "googleapis-com", "Gmail (work)"),
        _vendor_entry("oar_b", "googleapis-com", "Calendar"),
        _vendor_entry("oar_c", "googleapis-com", "Inactive elsewhere"),
        _vendor_entry(None, "github", "GitHub"),
    ]
    monkeypatch.setattr(tools_mod, "VendorRegistryService", _FakeVendorRegistryService)
    _FakeConnectSessionService.error = AmbiguousVendorError(
        "googleapis-com", None, ["oar_a", "oar_b"]
    )
    result = await dispatch_tool_call(
        _env(["credentials:connect", "capabilities:read"]),
        "request_connection",
        {"vendor": "googleapis-com"},
    )
    assert result.is_error

    payload = _payload(result)
    assert payload["error_code"] == "RESOLVE_FAILED"
    assert payload["next_tool"] == "request_connection"
    assert payload["actionable_step"] == tools_mod._ambiguous_vendor_actionable(
        "googleapis-com", listed=True
    )
    assert "your human user" in payload["actionable_step"]
    assert "oauth_app_registration_id" in payload["actionable_step"]
    assert payload["details"]["candidates"] == [
        {"registration_id": "oar_a", "name": "Gmail (work)", "display_name": "Google"},
        {"registration_id": "oar_b", "name": "Calendar", "display_name": "Google"},
    ]


async def test_ambiguous_vendor_without_capabilities_read_still_asks_the_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The candidate list rides the GET /vendors gate (capabilities:read), like
    the Go mount's read; without it the advice still routes to the user."""
    _FakeVendorRegistryService.entries = [_vendor_entry("oar_a", "googleapis-com", "Gmail")]
    monkeypatch.setattr(tools_mod, "VendorRegistryService", _FakeVendorRegistryService)
    _FakeConnectSessionService.error = AmbiguousVendorError(
        "googleapis-com", None, ["oar_a", "oar_b"]
    )
    result = await dispatch_tool_call(
        _env(["credentials:connect"]), "request_connection", {"vendor": "googleapis-com"}
    )
    payload = _payload(result)
    assert "details" not in payload
    assert payload["actionable_step"] == tools_mod._ambiguous_vendor_actionable(
        "googleapis-com", listed=False
    )


async def test_ambiguous_vendor_advice_matches_the_go_mount() -> None:
    """Cross-mount text parity with ``ambiguousVendorActionable`` (Go)."""
    assert tools_mod._ambiguous_vendor_actionable("googleapis-com", listed=True) == (
        'Several shared OAuth apps serve vendor "googleapis-com", and choosing one is '
        "your user's decision, not yours: show your human user the apps in "
        "details.candidates (name and registration_id) and ask which one to use, then "
        "call request_connection again with oauth_app_registration_id set to the "
        "registration_id they pick."
    )


async def test_invalid_registration_points_at_the_unpinned_retry() -> None:
    _FakeConnectSessionService.error = InvalidOAuthAppRegistrationError("oar_x", "inactive")
    result = await dispatch_tool_call(
        _env(["credentials:connect"]),
        "request_connection",
        {"vendor": "googleapis-com", "oauth_app_registration_id": "oar_x"},
    )
    payload = _payload(result)
    assert payload["error_code"] == "RESOLVE_FAILED"
    assert payload["next_tool"] == "request_connection"
    assert "without oauth_app_registration_id" in payload["actionable_step"]
    # The route's uniform refusal: the cause never reaches the caller.
    assert "inactive" not in json.dumps(payload)


# ── pin, rules, and the auto-importer (parity with the HTTP route) ───────────


async def test_pin_rules_and_auto_importer_reach_create_session() -> None:
    importer = object()
    result = await dispatch_tool_call(
        _env(["credentials:connect"], catalog_auto_importer=importer),
        "request_connection",
        {
            "vendor": "googleapis-com",
            "registration_id": "oar_a",
            "permission_rules": json.dumps(
                [{"effect": "allow", "methods": ["GET"], "path": "/gmail/.*"}]
            ),
        },
    )
    assert not result.is_error, result.content

    (call,) = _FakeConnectSessionService.calls
    assert call["oauth_app_registration_id"] == "oar_a"
    # The route's wire shape: model_dump(exclude_none=True), match_mode defaulted.
    assert call["requested_permission_rules"] == [
        {"effect": "allow", "methods": ["GET"], "path": "/gmail/.*", "match_mode": "regex"}
    ]
    assert _FakeConnectSessionService.importers == [importer]


@pytest.mark.parametrize(
    "rules",
    [
        {"effect": "allow"},  # not a list
        [{"effect": "allow", "pattern": "/x"}],  # unknown key
        [{"methods": ["GET"]}],  # missing effect
        [{"effect": "allow"}],  # condition-less allow
        [{"effect": "allow", "path": "("}],  # invalid regex
    ],
)
async def test_malformed_rules_are_invalid_params(rules: Any) -> None:
    with pytest.raises(MCPError) as err:
        await tools_mod.handle_request_connection(
            _env(["credentials:connect"]),
            {"vendor": "github", "requested_permission_rules": rules},
        )
    assert "requested_permission_rules" in str(err.value)
    assert _FakeConnectSessionService.calls == []


async def test_too_many_rules_is_invalid_params() -> None:
    rules = [{"effect": "deny", "methods": ["DELETE"]}] * 101
    with pytest.raises(MCPError) as err:
        await tools_mod.handle_request_connection(
            _env(["credentials:connect"]),
            {"vendor": "github", "requested_permission_rules": rules},
        )
    assert "100" in str(err.value)


async def test_overlong_registration_is_invalid_params() -> None:
    with pytest.raises(MCPError) as err:
        await tools_mod.handle_request_connection(
            _env(["credentials:connect"]),
            {"vendor": "github", "oauth_app_registration_id": "x" * 31},
        )
    assert "30" in str(err.value)


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
