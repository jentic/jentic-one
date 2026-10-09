"""Unit tests for the broker problem+json builder and error taxonomy mapping."""

from __future__ import annotations

import json
import typing
from typing import Any, cast

import pytest

from jentic_one.broker.core.exceptions import (
    AgentDirective,
    AgentStrategy,
    CredentialNeedsReconnectError,
    CredentialNotProvisionedError,
    CredentialRefreshTransientError,
    CredentialUndecryptableError,
    ErrorOrigin,
    OperationNotFoundError,
    UpstreamTimeoutError,
    ambiguous_credential_binding_directive,
    credential_identity_mismatch_directive,
    direct_action_denied_directive,
    direct_credential_identity_mismatch_directive,
    no_credential_binding_directive,
    suggested_permission_rules,
    switch_toolkit_directive,
)
from jentic_one.broker.core.headers import JenticHeader
from jentic_one.broker.core.problem import STATUS_BY_ERROR
from jentic_one.broker.web.errors import handle_broker_error, problem_response
from jentic_one.control.web.schemas.permission_rules import PermissionRuleSchema
from jentic_one.shared.access_guidance import ConnectTarget
from jentic_one.shared.broker.protocols import IdentityMismatch


def _body(resp: Any) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(bytes(resp.body)))


_ALLOWED_STRATEGIES = set(typing.get_args(AgentStrategy))


def test_directive_factories_emit_known_strategies() -> None:
    """Every directive factory must emit a strategy in the AgentStrategy
    vocabulary. This is the recovery contract autonomous agents depend on; a
    factory drifting to an unknown strategy (or the enum being trimmed) would
    silently break the loop. The Go ``agentDirective`` struct
    (cli/internal/cmd/execute.go) mirrors these same values by hand — keep them
    in lock-step until the contract is a shared OpenAPI schema (review P1-1)."""
    directives = [
        switch_toolkit_directive(503),
        credential_identity_mismatch_directive(
            mismatch=IdentityMismatch(
                expected_vendor="acme",
                expected_name="widgets",
                expected_version="1.0.0",
                found_vendor="acme",
                found_name="gadgets",
                found_version="1.0.0",
                would_match_if_normalized=False,
            )
        ),
        no_credential_binding_directive(
            vendor="acme", name="widgets", version="1.0.0", api_served=True
        ),
        ambiguous_credential_binding_directive(["cred_a", "cred_b"]),
        direct_credential_identity_mismatch_directive(
            mismatch=IdentityMismatch(
                expected_vendor="acme",
                expected_name="widgets",
                expected_version="1.0.0",
                found_vendor="acme",
                found_name="gadgets",
                found_version="1.0.0",
                would_match_if_normalized=False,
            )
        ),
        direct_action_denied_directive(),
    ]
    for d in directives:
        assert d.strategy in _ALLOWED_STRATEGIES, d.strategy


def test_no_credential_binding_directive_names_surviving_commands() -> None:
    """The default-path missing-binding directive routes to the operator.

    Access requests are retired: neither variant may emit a
    ``suggested_command`` naming the removed ``jentic access`` group. Off the
    vendor registry (``connect_vendor=None``, the default) no command is
    fabricated at all: served → ask the operator to bind to the serving
    credential; unserved → ask the operator to connect/provision one first.
    Neither variant may reference the retired toolkit vocabulary.
    """
    served = no_credential_binding_directive(
        vendor="acme", name="widgets", version="1.0.0", api_served=True
    )
    assert served.strategy == "prompt_human"
    assert served.parameters["api_served"] is True
    assert "suggested_command" not in served.parameters
    assert "operator" in served.human_readable_instruction
    assert "toolkit" not in served.human_readable_instruction.lower()

    unserved = no_credential_binding_directive(
        vendor="acme", name="widgets", version="1.0.0", api_served=False
    )
    assert unserved.strategy == "prompt_human"
    assert unserved.parameters["api_served"] is False
    assert "suggested_command" not in unserved.parameters
    instruction = unserved.human_readable_instruction
    assert "operator" in instruction
    assert "provision" in instruction
    assert "toolkit" not in instruction.lower()


def test_no_credential_binding_directive_registry_vendor_suggests_connect() -> None:
    """When a connect target covers the API, the provisioning leg is
    agent-initiable — the directive carries a runnable ``suggested_command``
    (``jentic connect <key>``, the connect key, never the API id), the
    structured ``connect`` object MCP clients fill ``request_connection``
    from, and the prose teaches the relay loop. Approval and the binding
    grant stay human in the wording."""
    unserved = no_credential_binding_directive(
        vendor="github.com",
        name="api.github.com",
        version="1.0.0",
        api_served=False,
        connect=ConnectTarget(vendor_key="github"),
    )
    assert unserved.strategy == "prompt_human"
    assert unserved.parameters["suggested_command"] == "jentic connect github"
    assert unserved.parameters["connect"] == {"vendor_key": "github"}
    instruction = unserved.human_readable_instruction
    assert "jentic connect github" in instruction
    assert "request_connection" in instruction
    assert "approval_url" in instruction

    served = no_credential_binding_directive(
        vendor="github.com",
        name="api.github.com",
        version="1.0.0",
        api_served=True,
        connect=ConnectTarget(vendor_key="github", registration_id="oar_1"),
    )
    # Served keeps the bind-me-first ask; connect is the alternative.
    assert served.parameters["suggested_command"] == "jentic connect github"
    assert served.parameters["connect"] == {"vendor_key": "github", "registration_id": "oar_1"}
    assert "bind" in served.human_readable_instruction


def test_no_credential_binding_directive_carries_suggested_rules() -> None:
    """``suggested_rules`` rides the directive whether or not the agent can
    connect itself: it is the minimal rule to ask for with the credential."""
    rules = suggested_permission_rules(method="post", path="/v1/widgets")
    assert rules == [
        {"effect": "allow", "methods": ["POST"], "path": "/v1/widgets", "match_mode": "exact"}
    ]
    off_registry = no_credential_binding_directive(
        vendor="acme", name="widgets", version="1.0.0", api_served=False, suggested_rules=rules
    )
    assert off_registry.parameters["suggested_rules"] == rules
    assert "connect" not in off_registry.parameters
    assert "suggested_command" not in off_registry.parameters


def test_suggested_permission_rules_match_the_rule_schema() -> None:
    """The suggestion is the shape the permission-rule authoring schema
    accepts, and an unusable path yields no suggestion rather than one the
    schema would refuse."""
    (rule,) = suggested_permission_rules(method="GET", path="/v1/pets/42") or []
    assert PermissionRuleSchema.model_validate(rule).match_mode == "exact"
    assert suggested_permission_rules(method="GET", path="") is None
    assert suggested_permission_rules(method="", path="/v1/pets") is None


@pytest.mark.parametrize("api_served", [False, True])
def test_no_credential_binding_directive_relays_the_open_session(api_served: bool) -> None:
    """With an open connect session, the agent relays its link instead of connecting again."""
    url = "https://j1.example.com/app/agents?approve=cs_1"
    d = no_credential_binding_directive(
        vendor="github.com",
        name="api.github.com",
        version="1.0.0",
        api_served=api_served,
        connect=ConnectTarget(vendor_key="github"),
        suggested_rules=[
            {"effect": "allow", "methods": ["GET"], "path": "/x", "match_mode": "exact"}
        ],
        provisioning_url=url,
    )
    assert d.strategy == "prompt_human"
    assert d.parameters["provisioning_url"] == url
    assert d.parameters["api_served"] is api_served
    # A second connect would only duplicate the pending request.
    assert "suggested_command" not in d.parameters
    assert "connect" not in d.parameters
    # The minimal rule still rides along for the approver.
    assert d.parameters["suggested_rules"][0]["path"] == "/x"
    assert url in d.human_readable_instruction
    assert "Do not start another" in d.human_readable_instruction


def test_no_credential_binding_directive_suggests_api_connect_when_enabled() -> None:
    """With API connect requests enabled (``connect_api``) and no vendor key,
    the directive names the API itself — ``suggested_command`` for the CLI
    and ``connect.api`` for an MCP client's ``request_connection`` — and
    teaches relay-then-end-your-turn. A vendor key still wins."""
    unserved = no_credential_binding_directive(
        vendor="acme",
        name="pets",
        version="v1",
        api_served=False,
        connect_api=True,
    )
    assert unserved.parameters["suggested_command"] == "jentic connect --api acme/pets/v1"
    assert unserved.parameters["connect"] == {
        "api": {"vendor": "acme", "name": "pets", "version": "v1"}
    }
    instruction = unserved.human_readable_instruction
    assert "jentic connect --api acme/pets/v1" in instruction
    assert "request_connection" in instruction
    assert "end your turn" in instruction
    assert "include the auth type" not in instruction

    served = no_credential_binding_directive(
        vendor="acme", name="pets", version="v1", api_served=True, connect_api=True
    )
    assert served.parameters["connect"]["api"]["name"] == "pets"
    assert "bind" in served.human_readable_instruction
    assert "jentic connect --api acme/pets/v1" in served.human_readable_instruction

    vendor_wins = no_credential_binding_directive(
        vendor="github.com",
        name="api.github.com",
        version="1.0.0",
        api_served=False,
        connect=ConnectTarget(vendor_key="github"),
        connect_api=True,
    )
    assert vendor_wins.parameters["suggested_command"] == "jentic connect github"
    assert vendor_wins.parameters["connect"] == {"vendor_key": "github"}

    # An open connect session still wins over the API target.
    pending = no_credential_binding_directive(
        vendor="acme",
        name="pets",
        version="v1",
        api_served=False,
        connect_api=True,
        provisioning_url="https://j1.example.com/app/agents?approve=cs_1",
    )
    assert "connect" not in pending.parameters
    assert "suggested_command" not in pending.parameters

    gate_off = no_credential_binding_directive(
        vendor="acme", name="pets", version="v1", api_served=False
    )
    assert "suggested_command" not in gate_off.parameters
    assert "connect" not in gate_off.parameters
    assert "include the auth type" in gate_off.human_readable_instruction


def test_ambiguous_credential_binding_directive_disambiguates_by_header() -> None:
    """The direct-path 409 twin retries via the Jentic-Credential-Id header.

    ``modify_headers`` + ``parameters.headers`` is the machine contract; the
    prose names ``Jentic-Credential-Name`` as the alternative when names are
    unique.
    """
    d = ambiguous_credential_binding_directive(["cred_a", "cred_b"])
    assert d.strategy == "modify_headers"
    assert d.parameters["candidates"] == ["cred_a", "cred_b"]
    assert d.parameters["headers"] == {"Jentic-Credential-Id": "cred_a"}
    assert "Jentic-Credential-Name" in d.human_readable_instruction


def test_credential_identity_mismatch_directive_has_no_fabricated_command() -> None:
    """The mismatch directive names expected/found but emits no CLI command.

    Fixing a credential is an operator action with no verbatim agent-runnable
    command, so a ``suggested_command`` here would be fiction the CLI prints
    verbatim (review B2)."""
    d = credential_identity_mismatch_directive(
        mismatch=IdentityMismatch(
            expected_vendor="acme",
            expected_name="widgets",
            expected_version="1.0.0",
            found_vendor="acme",
            found_name="gadgets",
            found_version="1.0.0",
            would_match_if_normalized=False,
        )
    )
    assert "suggested_command" not in d.parameters
    assert d.parameters["expected"]["name"] == "widgets"
    assert d.parameters["found"]["name"] == "gadgets"
    assert d.parameters["would_match_if_normalized"] is False
    # Expected/found identities must appear so the operator can act.
    assert "acme/widgets/1.0.0" in d.human_readable_instruction
    assert "acme/gadgets/1.0.0" in d.human_readable_instruction


def test_credential_identity_mismatch_directive_renders_unset_axes() -> None:
    """A vendor-wide (unset name) found identity renders ``vendor/*/version`` (review N6).

    Without the ``*`` placeholder, ``(acme, None, "1.0.0")`` would render as the
    ambiguous ``acme/1.0.0`` — indistinguishable from vendor/name."""
    d = credential_identity_mismatch_directive(
        mismatch=IdentityMismatch(
            expected_vendor="acme",
            expected_name="widgets",
            expected_version="1.0.0",
            found_vendor="acme",
            found_name=None,
            found_version="1.0.0",
            would_match_if_normalized=False,
        )
    )
    assert "acme/*/1.0.0" in d.human_readable_instruction


def test_credential_identity_mismatch_directive_would_normalize_message() -> None:
    """When only normalization differs, the instruction says so (#746 legacy row)."""
    d = credential_identity_mismatch_directive(
        mismatch=IdentityMismatch(
            expected_vendor="acme",
            expected_name="widgets",
            expected_version="1.0.0",
            found_vendor="Acme.com",
            found_name="Widgets",
            found_version="1.0.0",
            would_match_if_normalized=True,
        )
    )
    assert "normaliz" in d.human_readable_instruction.lower()
    assert d.parameters["would_match_if_normalized"] is True


def test_problem_response_defaults() -> None:
    resp = problem_response(404, "not found")
    assert resp.status_code == 404
    assert resp.media_type == "application/problem+json"
    body = _body(resp)
    assert body["type"] == "about:blank"
    assert body["title"] == "not found"
    assert body["status"] == 404
    assert body["error_origin"] == "broker"
    assert "agent_directive" not in body


def test_problem_response_sets_origin_header() -> None:
    resp = problem_response(504, "timeout", origin=ErrorOrigin.UPSTREAM)
    assert resp.headers[JenticHeader.ERROR_ORIGIN.value] == "upstream"
    assert _body(resp)["error_origin"] == "upstream"


def test_problem_response_embeds_directive() -> None:
    directive = switch_toolkit_directive(503)
    resp = problem_response(503, "bad gateway", directive=directive)
    body = _body(resp)
    assert body["agent_directive"]["strategy"] == "switch_toolkit"
    assert body["agent_directive"]["parameters"]["upstream_status"] == 503


def test_problem_response_merges_extra_and_headers() -> None:
    resp = problem_response(
        429,
        "slow down",
        extra={"retry_after_seconds": 5},
        headers={"Retry-After": "5"},
    )
    assert resp.headers["Retry-After"] == "5"
    assert _body(resp)["retry_after_seconds"] == 5


def test_status_table_maps_taxonomy() -> None:
    assert STATUS_BY_ERROR[OperationNotFoundError] == 404
    assert STATUS_BY_ERROR[UpstreamTimeoutError] == 504


def test_status_table_maps_credential_errors() -> None:
    # These must map to specific statuses, not fall through to a bare
    # BrokerError -> 500.
    assert STATUS_BY_ERROR[CredentialNotProvisionedError] == 424
    assert STATUS_BY_ERROR[CredentialUndecryptableError] == 424
    assert STATUS_BY_ERROR[CredentialNeedsReconnectError] == 401
    assert STATUS_BY_ERROR[CredentialRefreshTransientError] == 502


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (CredentialNotProvisionedError("nope"), 424),
        (CredentialUndecryptableError("undecryptable"), 424),
        (CredentialNeedsReconnectError("reconnect"), 401),
        (CredentialRefreshTransientError("transient"), 502),
        (OperationNotFoundError("missing"), 404),
    ],
)
async def test_handler_maps_credential_errors(error: Any, expected_status: int) -> None:
    resp = await handle_broker_error(cast(Any, None), error)
    assert resp.status_code == expected_status


def test_broker_error_carries_contract() -> None:
    err = OperationNotFoundError(
        "no match",
        origin=ErrorOrigin.BROKER,
        directive=AgentDirective(strategy="fatal", human_readable_instruction="give up"),
    )
    assert err.detail == "no match"
    assert err.origin is ErrorOrigin.BROKER
    assert err.directive is not None
    assert err.directive.strategy == "fatal"
