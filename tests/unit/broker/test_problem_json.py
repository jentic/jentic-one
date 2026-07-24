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
    action_denied_directive,
    ambiguous_toolkit_directive,
    credential_identity_mismatch_directive,
    no_toolkit_binding_directive,
    switch_toolkit_directive,
)
from jentic_one.broker.core.headers import JenticHeader
from jentic_one.broker.web.errors import STATUS_BY_ERROR, handle_broker_error, problem_response
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
        no_toolkit_binding_directive(
            vendor="acme", name="widgets", version="1.0.0", toolkit_serves_api=True
        ),
        ambiguous_toolkit_directive(["tk_a", "tk_b"]),
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
        action_denied_directive(),
    ]
    for d in directives:
        assert d.strategy in _ALLOWED_STRATEGIES, d.strategy


def test_ambiguous_toolkit_suggested_command_is_runnable() -> None:
    """The disambiguation command must be copy-pasteable — not a template with a
    literal ellipsis the CLI would print verbatim (review P3-1)."""
    d = ambiguous_toolkit_directive(["tk_a", "tk_b"])
    cmd = d.parameters["suggested_command"]
    assert "…" not in cmd
    assert "Jentic-Toolkit-Id=tk_a" in cmd


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
    # Regression: these previously fell through to a bare BrokerError -> 500.
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
