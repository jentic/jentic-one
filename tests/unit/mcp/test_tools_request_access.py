"""request_access on the mount — the Go table tests, replayed against the port.

Mirrors ``cli/internal/cli/api/mcp_access_test.go``'s request_access coverage
(arm exclusivity, duplicate attach-vs-composite, terminal-state mapping,
approve_url absolutization) plus the arms only the in-process port has: the
pydantic validation round-trip, the ``_require_db`` refusal, and the honesty
branch that replaces the CLI's token re-mint (scopes are drawn live per
request, so the only question is whether THIS session's consent covers the
granted scope). Deliberately absent, per the plan: Go's ``awaitAutoDecision``
post-file poll (this backend has no file-time auto-decision — ``file()``
always leaves the request PENDING).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, ClassVar, cast
from unittest.mock import MagicMock

import pytest
from mcp.shared.exceptions import MCPError

import jentic_one.mcp.tools as tools_mod
from jentic_one.control.services.access_requests.errors import (
    AccessRequestNotFoundError,
    DuplicatePendingError,
    PrerequisiteNotMetError,
    UnsupportedScopeGrantError,
)
from jentic_one.control.services.access_requests.schemas.access_requests import (
    AccessRequestItemView,
    AccessRequestView,
)
from jentic_one.mcp.tools import CallEnv, absolutize_approve_url, dispatch_tool_call
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AuthConfig, ServerConfig
from jentic_one.shared.models import ActorType

_NOW = datetime(2026, 9, 10, tzinfo=UTC)


def _env(permissions: list[str] | None = None) -> CallEnv:
    ctx = MagicMock()
    ctx.config.auth = AuthConfig(canonical_base_url="https://auth.example.com")
    ctx.config.server = ServerConfig()
    ctx.instance_id = None
    return CallEnv(
        ctx=ctx,
        identity=Identity(sub="agnt_1", permissions=permissions or [], actor_type=ActorType.AGENT),
        credential="jak_test",
        base_url="https://auth.example.com",
        session_id=None,
    )


def _payload(result: Any) -> dict[str, Any]:
    (content,) = result.content
    decoded = json.loads(content.text)
    assert isinstance(decoded, dict)
    return decoded


def _item(
    resource_type: str = "toolkit",
    action: str = "bind",
    status: str = "pending",
    resource_id: str | None = None,
    decision_reason: str | None = None,
) -> AccessRequestItemView:
    return AccessRequestItemView(
        id="item_1",
        resource_type=resource_type,
        action=action,
        resource_id=resource_id,
        resource_reference=None,
        to_type=None,
        to_id=None,
        rules=None,
        status=status,
        applied_effects=None,
        decided_by=None,
        decided_at=None,
        decision_reason=decision_reason,
    )


def _view(
    status: str = "pending",
    *,
    request_id: str = "acr_1",
    approve_url: str = "/access-requests/acr_1",
    items: list[AccessRequestItemView] | None = None,
) -> AccessRequestView:
    return AccessRequestView(
        id=request_id,
        actor_id="agnt_1",
        reason="read invoices for the summary task",
        requested_by="agnt_1",
        status=status,
        approve_url=approve_url,
        filed_at=_NOW,
        expires_at=_NOW,
        created_by="agnt_1",
        filer_owner_id="usr_1",
        items=items if items is not None else [_item()],
    )


class _FakeAccessRequestService:
    """AccessRequestService stand-in: scripted file()/get() outcomes."""

    filed: ClassVar[list[dict[str, Any]]] = []
    file_result: ClassVar[AccessRequestView | None] = None
    file_error: ClassVar[Exception | None] = None
    get_results: ClassVar[dict[str, AccessRequestView]] = {}
    get_error: ClassVar[Exception | None] = None
    gets: ClassVar[list[str]] = []

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    async def file(
        self,
        *,
        actor_id: str,
        reason: str | None,
        items: list[dict[str, Any]],
        identity: Identity,
    ) -> AccessRequestView:
        _FakeAccessRequestService.filed.append(
            {"actor_id": actor_id, "reason": reason, "items": items}
        )
        if _FakeAccessRequestService.file_error is not None:
            raise _FakeAccessRequestService.file_error
        assert _FakeAccessRequestService.file_result is not None
        return _FakeAccessRequestService.file_result

    async def get(self, request_id: str, *, identity: Identity) -> AccessRequestView:
        _FakeAccessRequestService.gets.append(request_id)
        if _FakeAccessRequestService.get_error is not None:
            raise _FakeAccessRequestService.get_error
        view = _FakeAccessRequestService.get_results.get(request_id)
        if view is None:
            raise AccessRequestNotFoundError(request_id)
        return view


@pytest.fixture()
def service(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wire the service seam to a fresh fake."""
    _FakeAccessRequestService.filed = []
    _FakeAccessRequestService.file_result = _view("pending")
    _FakeAccessRequestService.file_error = None
    _FakeAccessRequestService.get_results = {}
    _FakeAccessRequestService.get_error = None
    _FakeAccessRequestService.gets = []
    monkeypatch.setattr(tools_mod, "AccessRequestService", _FakeAccessRequestService)


# ── arm exclusivity (Go: MissingTargetIsInvalidParams / RequestIDPlusTargets /
#    PollArmRejectsStrayFilingParams) ─────────────────────────────────────────


async def test_no_target_and_no_request_id_is_invalid_params(service: None) -> None:
    with pytest.raises(MCPError) as err:
        await tools_mod.handle_request_access(_env(), {})
    for name in ("provision", "toolkits", "scopes", "request_id"):
        assert name in str(err.value)
    assert _FakeAccessRequestService.filed == []


@pytest.mark.parametrize(
    "arguments",
    [
        {"request_id": "acr_1", "toolkits": ["acme/pets"]},
        {"request_id": "acr_1", "reason": "please"},
        {"request_id": "acr_1", "auth": ["bearer"]},
        {"request_id": "acr_1", "rules_json": [{"effect": "allow"}]},
        {"request_id": "acr_1", "provision": ["acme/pets"]},
    ],
)
async def test_request_id_plus_filing_params_is_invalid_params(
    service: None, arguments: dict[str, Any]
) -> None:
    """Filing params riding along with request_id are a confused call, not
    noise to drop — silently ignoring them would teach the model its
    arguments were accepted."""
    with pytest.raises(MCPError, match="not both"):
        await tools_mod.handle_request_access(_env(), arguments)
    assert _FakeAccessRequestService.gets == [], "the confused call must not poll"


async def test_malformed_rules_json_with_request_id_is_invalid_params(service: None) -> None:
    """Go's poll-arm symmetry: a malformed rules_json is invalid_params on
    BOTH arms — never silently dropped on the poll arm."""
    with pytest.raises(MCPError, match="rules_json"):
        await tools_mod.handle_request_access(_env(), {"request_id": "acr_1", "rules_json": 42})


async def test_compose_conflict_is_invalid_params(service: None) -> None:
    result = None
    with pytest.raises(MCPError, match="provisioning plan already ends"):
        result = await tools_mod.handle_request_access(
            _env(), {"provision": ["acme/pets"], "toolkits": ["acme/pets"]}
        )
    assert result is None
    assert _FakeAccessRequestService.filed == []


# ── the filing arm ────────────────────────────────────────────────────────────


async def test_filing_returns_pending_envelope_immediately_with_instruction(
    service: None,
) -> None:
    """The full-composite happy path (Go: FilesComposedPlanPendingWithApproveURL),
    minus the post-file poll: file() always leaves the request PENDING on this
    backend (no file-time auto-decision exists), so the PENDING envelope
    returns immediately — exactly one service call, the file()."""
    env = _env()
    result = await dispatch_tool_call(
        env,
        "request_access",
        {
            "provision": ["stripe.com/api"],
            "auth": ["bearer"],
            "rules_json": [{"effect": "allow", "methods": ["GET"], "path": ".*"}],
            "toolkits": ["github.com/api"],
            "scopes": ["catalog:import"],
            "reason": "read invoices for the summary task",
        },
    )
    assert not result.is_error, result.content
    payload = _payload(result)
    assert payload["schema_version"] == "1"
    assert payload["id"] == "acr_1"
    assert payload["status"] == "pending"
    assert "never approves" in payload["instruction"]
    assert "request_id" in payload["instruction"]
    assert "instance" in payload

    (call,) = _FakeAccessRequestService.filed
    assert call["actor_id"] == "agnt_1"
    assert call["reason"] == "read invoices for the summary task"
    kinds = [(i["resource_type"], i["action"]) for i in call["items"]]
    assert kinds == [
        ("toolkit", "create"),
        ("credential", "provision"),
        ("credential", "bind"),
        ("toolkit", "bind"),
        ("toolkit", "bind"),
        ("scope", "grant"),
    ], "compose() fulfilment order: the 4-item chain, the bind, the grant"
    assert call["items"][1]["resource_reference"]["security_scheme"] == "bearer"
    assert call["items"][2]["rules"][0]["methods"] == ["GET"], "rules never comma-split"
    assert call["items"][5]["resource_id"] == "catalog:import"
    assert _FakeAccessRequestService.gets == [], "no post-file poll on this backend"


async def test_filing_absolutizes_the_relative_approve_url(service: None) -> None:
    """The service stores ``{canonical_base_url}/access-requests/{id}`` which
    is a rooted RELATIVE path when the knob is unset — absolutized onto
    env.base_url for the human operator."""
    result = await dispatch_tool_call(_env(), "request_access", {"toolkits": ["acme/pets"]})
    payload = _payload(result)
    assert payload["approve_url"] == "https://auth.example.com/access-requests/acr_1"


async def test_pydantic_validation_rejects_mis_shaped_rules(service: None) -> None:
    """Validation parity: composed items round-trip the REST schemas, so a
    rules_json whose rules are mis-shaped (a bad effect) is invalid_params —
    it must never reach file() with less validation than REST applies."""
    with pytest.raises(MCPError, match="invalid access-request items"):
        await tools_mod.handle_request_access(
            _env(),
            {
                "provision": ["acme/pets"],
                "rules_json": [{"effect": "shrug", "path": ".*"}],
            },
        )
    assert _FakeAccessRequestService.filed == [], "validation must refuse before filing"


async def test_file_time_policy_exceptions_map_to_invalid_params(service: None) -> None:
    """The service's own validation-shaped file-time refusals (REST: 422) are
    correctable calls."""
    _FakeAccessRequestService.file_error = UnsupportedScopeGrantError("org:admin")
    with pytest.raises(MCPError, match="org:admin"):
        await tools_mod.handle_request_access(_env(), {"scopes": ["org:admin"]})


async def test_prerequisite_refusal_is_the_residual_403_arm(service: None) -> None:
    """A permission-shaped service refusal of the FILING itself: BROKER_DENIED
    pointing at whoami — NOT request_access (an agent that may not file
    requests cannot request the right to file them), NOT get_started (not on
    this surface)."""
    _FakeAccessRequestService.file_error = PrerequisiteNotMetError("agnt_1", "tk_1", "credential")
    result = await dispatch_tool_call(_env(), "request_access", {"toolkits": ["acme/pets"]})
    assert result.is_error
    payload = _payload(result)
    assert payload["error_code"] == "BROKER_DENIED"
    assert payload["next_tool"] == "whoami"
    assert "operator" in payload["actionable_step"]


# ── duplicate handling (Go: DuplicatePendingSingleTargetAttaches / CompositeIsSoftError) ──


async def test_duplicate_single_target_attaches_to_the_existing_request(service: None) -> None:
    _FakeAccessRequestService.file_error = DuplicatePendingError(
        approve_url="/access-requests/acr_old", existing_request_id="acr_old"
    )
    _FakeAccessRequestService.get_results["acr_old"] = _view(
        "pending", request_id="acr_old", approve_url="/access-requests/acr_old"
    )
    result = await dispatch_tool_call(_env(), "request_access", {"toolkits": ["acme/pets"]})
    assert not result.is_error, "a single-target duplicate attaches, like the CLI"
    payload = _payload(result)
    assert payload["id"] == "acr_old"
    assert payload["attached_to_existing"] is True
    assert "instruction" in payload, "the attached pending request keeps the poll instruction"


async def test_duplicate_composite_is_an_honest_nothing_was_filed_error(service: None) -> None:
    """Filing is all-or-nothing: a duplicate on a composite means NOTHING was
    filed — attaching would silently swap the composite for the older,
    smaller request."""
    _FakeAccessRequestService.file_error = DuplicatePendingError(
        approve_url="/access-requests/acr_old", existing_request_id="acr_old"
    )
    result = await dispatch_tool_call(
        _env(),
        "request_access",
        {"toolkits": ["acme/pets"], "scopes": ["catalog:import"]},
    )
    assert result.is_error, "a composite collision files NOTHING and must not read as success"
    payload = _payload(result)
    assert payload["error_code"] == "RESOLVE_FAILED"
    assert "nothing was filed" in payload["error"]
    assert payload["details"] == {"existing_request_id": "acr_old"}
    assert payload["next_tool"] == "request_access"
    assert _FakeAccessRequestService.gets == [], "the composite arm never silently attaches"


async def test_duplicate_attach_fetch_failure_surfaces_the_existing_id(service: None) -> None:
    """The existing request should always be fetchable (the duplicate check is
    actor-scoped) — if it is not, the failure surfaces with the id, never a
    silent swap or a fake success."""
    _FakeAccessRequestService.file_error = DuplicatePendingError(
        approve_url="", existing_request_id="acr_old"
    )
    _FakeAccessRequestService.get_results = {}  # the fetch will miss
    result = await dispatch_tool_call(_env(), "request_access", {"toolkits": ["acme/pets"]})
    assert result.is_error
    payload = _payload(result)
    assert payload["error_code"] == "INTERNAL_ERROR"
    assert payload["details"] == {"existing_request_id": "acr_old"}


# ── the poll arm (Go: PollArmReportsApproved + the 404 mapping) ──────────────


async def test_poll_arm_returns_the_full_request_object(service: None) -> None:
    _FakeAccessRequestService.get_results["acr_1"] = _view(
        "approved", items=[_item(status="approved")]
    )
    result = await dispatch_tool_call(_env(), "request_access", {"request_id": "acr_1"})
    assert not result.is_error, "an approved request is a normal result"
    payload = _payload(result)
    assert payload["id"] == "acr_1"
    assert payload["status"] == "approved"
    assert payload["schema_version"] == "1"
    assert payload["items"][0]["status"] == "approved"
    assert "instruction" not in payload, (
        "no scope was granted, so neither the pending instruction nor the "
        "scope honesty wording applies"
    )
    assert _FakeAccessRequestService.gets == ["acr_1"]
    assert _FakeAccessRequestService.filed == [], "the poll arm never files"


async def test_poll_arm_uses_the_id_alias(service: None) -> None:
    _FakeAccessRequestService.get_results["acr_1"] = _view("pending")
    result = await dispatch_tool_call(_env(), "request_access", {"id": "acr_1"})
    assert not result.is_error
    assert _payload(result)["status"] == "pending"


async def test_poll_arm_unknown_id_is_resolve_failed_with_self_pointer(service: None) -> None:
    """A 404-shaped miss (unknown id, or row-filtered out of this caller's
    visibility): the identity resolved — the id is wrong; the recovery is
    re-reading the earlier request_access result."""
    result = await dispatch_tool_call(_env(), "request_access", {"request_id": "acr_nope"})
    assert result.is_error
    payload = _payload(result)
    assert payload["error_code"] == "RESOLVE_FAILED"
    assert "acr_nope" in payload["error"]
    assert "re-check the request id" in payload["actionable_step"].lower()
    assert payload["next_tool"] == "request_access"


# ── terminal-state mapping (Go: accessRequestResult) ─────────────────────────


async def test_denied_maps_to_broker_denied_with_the_request_attached(service: None) -> None:
    """The denial reads decision_reason first: the full request rides the
    error's ``request`` extra so the model can learn WHY before giving up."""
    _FakeAccessRequestService.get_results["acr_1"] = _view(
        "denied",
        items=[_item(status="denied", decision_reason="No toolkit serves API acme/pets")],
    )
    result = await dispatch_tool_call(_env(), "request_access", {"request_id": "acr_1"})
    assert result.is_error, "a denied request must never look like success"
    payload = _payload(result)
    assert payload["error_code"] == "BROKER_DENIED"
    assert "decision_reason" in payload["actionable_step"]
    assert "provision" in payload["actionable_step"]
    assert payload["next_tool"] == "whoami"
    request = payload["request"]
    assert request["items"][0]["decision_reason"] == "No toolkit serves API acme/pets"
    assert request["schema_version"] == "1"


@pytest.mark.parametrize("status", ["expired", "withdrawn"])
async def test_expired_and_withdrawn_point_at_a_fresh_filing(service: None, status: str) -> None:
    _FakeAccessRequestService.get_results["acr_1"] = _view(status)
    result = await dispatch_tool_call(_env(), "request_access", {"request_id": "acr_1"})
    assert result.is_error
    payload = _payload(result)
    assert payload["error_code"] == "BROKER_DENIED"
    assert status in payload["error"]
    assert "nothing was granted" in payload["error"]
    assert "fresh request_access" in payload["actionable_step"]
    assert payload["next_tool"] == "request_access"
    assert payload["request"]["id"] == "acr_1"


async def test_partially_approved_mints_the_partial_approval_code(service: None) -> None:
    """PARTIAL_APPROVAL — the Go stdio server's wire code, now on this door
    too: proceed only with what was approved."""
    _FakeAccessRequestService.get_results["acr_1"] = _view(
        "partially_approved",
        items=[_item(status="approved"), _item(status="denied")],
    )
    result = await dispatch_tool_call(_env(), "request_access", {"request_id": "acr_1"})
    assert result.is_error
    payload = _payload(result)
    assert payload["error_code"] == "PARTIAL_APPROVAL"
    assert "items[].status" in payload["actionable_step"]
    assert payload["next_tool"] == "whoami"
    assert payload["request"]["status"] == "partially_approved"


# ── the honesty branch (replaces the CLI's token re-mint) ────────────────────


async def test_granted_scope_in_session_permissions_reads_active_now(service: None) -> None:
    """Scopes are drawn live per request: when the poll observing the
    approval already carries the granted scope, the wording says so."""
    _FakeAccessRequestService.get_results["acr_1"] = _view(
        "approved",
        items=[_item("scope", "grant", status="approved", resource_id="catalog:import")],
    )
    env = _env(["catalog:import"])
    result = await dispatch_tool_call(env, "request_access", {"request_id": "acr_1"})
    assert not result.is_error
    payload = _payload(result)
    assert "active now" in payload["instruction"]
    assert "catalog:import" in payload["instruction"]


async def test_granted_scope_absent_from_session_never_promises_a_retry(service: None) -> None:
    """The consent-ceiling edge: the grant is live server-side, but THIS
    session's consent does not cover it — say re-authorization is required,
    never 'retry and it works'."""
    _FakeAccessRequestService.get_results["acr_1"] = _view(
        "approved",
        items=[_item("scope", "grant", status="approved", resource_id="catalog:import")],
    )
    env = _env([])  # the session ceiling excludes the granted scope
    result = await dispatch_tool_call(env, "request_access", {"request_id": "acr_1"})
    assert not result.is_error
    payload = _payload(result)
    assert "does not cover" in payload["instruction"]
    assert "re-authorization" in payload["instruction"]
    assert "retry" not in payload["instruction"].split("Do not assume")[0], (
        "the missing-scope wording must not promise a retry"
    )


async def test_partial_approval_carries_the_honesty_instruction_too(service: None) -> None:
    _FakeAccessRequestService.get_results["acr_1"] = _view(
        "partially_approved",
        items=[
            _item("scope", "grant", status="approved", resource_id="catalog:import"),
            _item(status="denied"),
        ],
    )
    env = _env(["catalog:import"])
    result = await dispatch_tool_call(env, "request_access", {"request_id": "acr_1"})
    payload = _payload(result)
    assert payload["error_code"] == "PARTIAL_APPROVAL"
    assert "active now" in payload["instruction"]


async def test_binding_only_approval_carries_no_scope_instruction(service: None) -> None:
    """Bindings are enforced live broker-side — nothing to say (the Go twin:
    a binding-only plan never triggers the re-mint)."""
    _FakeAccessRequestService.get_results["acr_1"] = _view(
        "approved", items=[_item(status="approved")]
    )
    result = await dispatch_tool_call(_env(), "request_access", {"request_id": "acr_1"})
    payload = _payload(result)
    assert "instruction" not in payload


async def test_denied_scope_grant_never_reads_as_granted(service: None) -> None:
    _FakeAccessRequestService.get_results["acr_1"] = _view(
        "approved",
        items=[_item("scope", "grant", status="denied", resource_id="catalog:import")],
    )
    result = await dispatch_tool_call(_env(), "request_access", {"request_id": "acr_1"})
    payload = _payload(result)
    assert "instruction" not in payload, "a denied grant must not trigger the honesty wording"


# ── approve_url absolutization (Go: TestAbsolutizeApproveURL_SchemeRelativeCleared) ──


@pytest.mark.parametrize(
    ("stored", "want"),
    [
        ("//evil.example/console/x", ""),
        ("/access-requests/acr_1", "https://control.example/access-requests/acr_1"),
        ("https://canonical.example/x", "https://canonical.example/x"),
        ("", ""),
        ("not-rooted/path", ""),
    ],
)
def test_absolutize_approve_url_posture(stored: str, want: str) -> None:
    """Scheme-relative would resolve onto a FOREIGN host → cleared; only
    rooted paths absolutize; an already-absolute URL wins over env.base_url
    (the two canonical-base knobs can disagree)."""
    assert absolutize_approve_url("https://control.example", stored) == want


async def test_poll_result_keeps_an_absolute_stored_approve_url(service: None) -> None:
    _FakeAccessRequestService.get_results["acr_1"] = _view(
        "pending", approve_url="https://canonical.example/access-requests/acr_1"
    )
    result = await dispatch_tool_call(_env(), "request_access", {"request_id": "acr_1"})
    payload = _payload(result)
    assert payload["approve_url"] == "https://canonical.example/access-requests/acr_1"


# ── DB-gate refusal ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("blocked", ["control", "admin"])
async def test_db_gate_refusal_is_a_soft_internal_error(service: None, blocked: str) -> None:
    """The service rides both planes (access-request tables on the control DB;
    owner/event resolution on the admin DB): a deployment shape missing either
    refuses softly before any filing."""
    env = _env()
    cast(MagicMock, env.ctx).is_db_allowed.side_effect = lambda db: db != blocked
    result = await dispatch_tool_call(env, "request_access", {"toolkits": ["acme/pets"]})
    assert result.is_error
    payload = _payload(result)
    assert payload["error_code"] == "INTERNAL_ERROR"
    assert "not available on this deployment" in payload["error"]
    assert _FakeAccessRequestService.filed == [], "the DB gate must refuse before filing"


async def test_no_scope_gate_on_filing(service: None) -> None:
    """POST /access-requests uses bare get_current_identity() with no
    required_permissions — a zero-scope identity can still file (an
    empty-list require_scopes would deny every non-admin)."""
    result = await dispatch_tool_call(_env([]), "request_access", {"toolkits": ["acme/pets"]})
    assert not result.is_error, "filing is not scope-gated on the REST route it fronts"
    assert len(_FakeAccessRequestService.filed) == 1
