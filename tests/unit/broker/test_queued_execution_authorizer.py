"""Unit tests for the run-time re-authorizer of queued executions.

Uses protocol fakes for the actor-status lookup and binding derivation (no DB):
the DB-backed behaviour is covered by
``tests/integration/broker/test_queued_execution_authorization.py``.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from jentic_one.broker.core.exceptions import ActionDeniedError, AgentDirective
from jentic_one.broker.core.problem import broker_error_problem, problem_body
from jentic_one.broker.repos.actor_status import ActorStatusResolver
from jentic_one.broker.services.execution.queued_authorization import QueuedExecutionAuthorizer
from jentic_one.shared.broker.protocols import (
    CredentialDerivation,
    RuleEvaluation,
    ToolkitDerivation,
)
from jentic_one.shared.config import VendorRegistryConfig
from jentic_one.shared.jobs.protocols import QueuedExecutionRequest


class _FakeActorStatus:
    def __init__(self, active: bool, *, scoped: bool = True) -> None:
        self._active = active
        self._scoped = scoped
        self.calls: list[tuple[str, str]] = []
        self.scope_calls: list[tuple[str, str, str]] = []

    async def is_active(self, *, actor_id: str, actor_type: str) -> bool:
        self.calls.append((actor_id, actor_type))
        return self._active

    async def holds_scope(self, *, actor_id: str, actor_type: str, scope: str) -> bool:
        self.scope_calls.append((actor_id, actor_type, scope))
        return self._scoped


class _FakeToolkitDeriver:
    """The toolkit path's derivation: one bound toolkit serving the API."""

    def __init__(self, toolkits: tuple[str, ...], credentials: tuple[str, ...]) -> None:
        self._toolkits = toolkits
        self._credentials = credentials

    async def derive_toolkits(
        self, *, agent_id: str, vendor: str, name: str, version: str
    ) -> ToolkitDerivation:
        return ToolkitDerivation(
            toolkits=self._toolkits,
            agent_bound_any=bool(self._toolkits),
            api_served_toolkits=self._toolkits,
            identity_mismatch=None,
            credentials_by_toolkit=dict.fromkeys(self._toolkits, self._credentials),
        )


class _FakeRuleEvaluator:
    def __init__(self, *, allowed: bool) -> None:
        self._allowed = allowed
        self.toolkit_ids: list[str] = []

    async def evaluate(
        self,
        *,
        toolkit_id: str,
        method: str,
        path: str,
        operation_id: str | None = None,
        api_vendor: str | None = None,
    ) -> RuleEvaluation:
        self.toolkit_ids.append(toolkit_id)
        return RuleEvaluation(allowed=self._allowed, rules_loaded=1)


class _DenyingCredentialDeriver:
    """A deriver whose binding lookup surfaces a denial (as the sync path would)."""

    def __init__(self) -> None:
        self.calls = 0

    async def derive_credentials(
        self, *, agent_id: str, vendor: str, name: str, version: str
    ) -> CredentialDerivation:
        self.calls += 1
        raise ActionDeniedError(
            "Denied by rule",
            type="action_denied",
            instance="/api.example.com/v1/things",
            directive=AgentDirective(
                strategy="retry", human_readable_instruction="Ask for access."
            ),
        )


def _authorizer(
    *,
    active: bool,
    scoped: bool = True,
    deriver: Any | None = None,
    toolkit_deriver: Any | None = None,
    rule_evaluator: Any | None = None,
) -> tuple[QueuedExecutionAuthorizer, _FakeActorStatus]:
    ctx = MagicMock()
    ctx.config.broker.direct_bindings_enabled = toolkit_deriver is None
    ctx.config.vendors = VendorRegistryConfig()
    status = _FakeActorStatus(active, scoped=scoped)
    unused = MagicMock()
    return (
        QueuedExecutionAuthorizer(
            ctx,
            actor_status=cast(ActorStatusResolver, status),
            deriver=toolkit_deriver or unused,
            rule_evaluator=rule_evaluator or unused,
            credential_deriver=deriver or _DenyingCredentialDeriver(),
            agent_rule_evaluator=unused,
        ),
        status,
    )


def _request(**overrides: Any) -> QueuedExecutionRequest:
    fields: dict[str, Any] = {
        "actor_id": "agt_1",
        "actor_type": "agent",
        "method": "GET",
        "upstream_url": "https://api.example.com/v1/things",
        "api_vendor": "example.com",
        "api_name": "things",
        "api_version": "v1",
        "credential_id": "cred_1",
    }
    fields.update(overrides)
    return QueuedExecutionRequest(**fields)


@pytest.mark.asyncio
async def test_inactive_actor_is_denied_before_any_binding_lookup() -> None:
    deriver = _DenyingCredentialDeriver()
    authorizer, status = _authorizer(active=False, deriver=deriver)

    verdict = await authorizer.authorize(_request())

    assert status.calls == [("agt_1", "agent")]
    assert deriver.calls == 0
    assert verdict.allowed is False
    assert verdict.allowed_credential_ids == ()
    assert verdict.credential_id is None
    assert verdict.problem == {
        "type": "unauthorized",
        "title": "Unauthorized",
        "status": 401,
        "detail": "The actor that queued this execution is no longer active.",
        "instance": "/api.example.com/v1/things",
    }


@pytest.mark.asyncio
async def test_revoked_execute_scope_is_denied_before_any_binding_lookup() -> None:
    deriver = _DenyingCredentialDeriver()
    authorizer, status = _authorizer(active=True, scoped=False, deriver=deriver)

    verdict = await authorizer.authorize(_request())

    assert status.scope_calls == [("agt_1", "agent", "capabilities:execute")]
    assert deriver.calls == 0
    assert verdict.allowed is False
    assert verdict.allowed_credential_ids == ()
    assert verdict.problem == {
        "type": "insufficient_scope",
        "title": "Forbidden",
        "status": 403,
        "detail": "Insufficient scope: 'capabilities:execute' required",
        "instance": "/api.example.com/v1/things",
    }


@pytest.mark.asyncio
async def test_toolkit_path_replays_the_enqueued_toolkit_and_rederives_the_boundary() -> None:
    rules = _FakeRuleEvaluator(allowed=True)
    authorizer, _ = _authorizer(
        active=True,
        toolkit_deriver=_FakeToolkitDeriver(("tk_1",), ("cred_now",)),
        rule_evaluator=rules,
    )

    verdict = await authorizer.authorize(_request(toolkit_id="tk_1", credential_id=None))

    assert verdict.allowed is True
    # The boundary is the toolkit's credentials *now*, not an enqueue snapshot.
    assert verdict.allowed_credential_ids == ("cred_now",)
    assert verdict.credential_id is None
    assert rules.toolkit_ids == ["tk_1"]


@pytest.mark.asyncio
async def test_toolkit_path_denies_when_the_enqueued_toolkit_is_no_longer_bound() -> None:
    rules = _FakeRuleEvaluator(allowed=True)
    authorizer, _ = _authorizer(
        active=True,
        toolkit_deriver=_FakeToolkitDeriver(("tk_other",), ("cred_other",)),
        rule_evaluator=rules,
    )

    verdict = await authorizer.authorize(_request(toolkit_id="tk_1"))

    assert verdict.allowed is False
    assert verdict.problem is not None
    assert verdict.problem["type"] == "toolkit_binding_required"
    assert verdict.problem["status"] == 403
    assert rules.toolkit_ids == []


@pytest.mark.asyncio
async def test_payload_without_api_identity_is_denied() -> None:
    deriver = _DenyingCredentialDeriver()
    authorizer, _ = _authorizer(active=True, deriver=deriver)

    verdict = await authorizer.authorize(_request(api_version=""))

    assert deriver.calls == 0
    assert verdict.allowed is False
    assert verdict.problem is not None
    assert verdict.problem["type"] == "operation_not_found"
    assert verdict.problem["status"] == 404


@pytest.mark.asyncio
async def test_policy_denial_carries_the_sync_problem_body() -> None:
    authorizer, _ = _authorizer(active=True)

    verdict = await authorizer.authorize(_request())

    assert verdict.allowed is False
    assert verdict.problem == {
        "type": "action_denied",
        "title": "Denied by rule",
        "status": 403,
        "error_origin": "broker",
        "instance": "/api.example.com/v1/things",
        "agent_directive": {
            "strategy": "retry",
            "parameters": {},
            "human_readable_instruction": "Ask for access.",
        },
    }


def test_broker_error_problem_matches_problem_body() -> None:
    exc = ActionDeniedError("nope", type="action_denied", extra={"k": "v"})

    assert broker_error_problem(exc) == problem_body(
        403, "nope", type="action_denied", extra={"k": "v"}
    )


def test_unmapped_broker_error_maps_to_500() -> None:
    class _UnmappedError(ActionDeniedError):
        pass

    assert broker_error_problem(_UnmappedError("x"))["status"] == 500
