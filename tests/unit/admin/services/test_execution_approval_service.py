"""Unit tests for ExecutionApprovalService pure-logic paths.

DB-dependent paths (CAS semantics, deny job_result, expiry sweep) require
live DB fixtures and belong in integration tests.
"""

from __future__ import annotations

import pytest

from jentic_one.admin.services.errors import (
    ExecutionApprovalAlreadyDecidedError,
    ExecutionApprovalForbiddenError,
)
from jentic_one.admin.services.execution_approval_service import ExecutionApprovalService
from jentic_one.admin.services.schemas.execution_approvals import DecideInput
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models.actors import ActorType


def _user_identity(sub: str = "usr_test") -> Identity:
    return Identity(sub=sub, actor_type=ActorType.USER)


def _agent_identity(sub: str = "agt_test") -> Identity:
    return Identity(sub=sub, actor_type=ActorType.AGENT)


async def test_decide_rejects_agent_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    """decide() raises ExecutionApprovalForbiddenError before any DB call for agent actors."""
    svc = object.__new__(ExecutionApprovalService)
    with pytest.raises(ExecutionApprovalForbiddenError):
        await svc.decide(
            "exap_001",
            DecideInput(decision="approved"),
            identity=_agent_identity(),
        )


async def test_decide_rejects_invalid_decision_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """decide() raises ExecutionApprovalAlreadyDecidedError for unrecognised decision strings."""
    svc = object.__new__(ExecutionApprovalService)
    with pytest.raises(ExecutionApprovalAlreadyDecidedError):
        await svc.decide(
            "exap_001",
            DecideInput(decision="maybe"),
            identity=_user_identity(),
        )


async def test_decide_approved_does_not_raise_for_user_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """decide() with a user identity passes guard checks.

    Any DB call raises AttributeError (no _ctx), confirming the code reached
    past the guard — intentional early-fail pattern.
    """
    svc = object.__new__(ExecutionApprovalService)
    # _ctx is not set, so the first DB call inside `decide()` will raise AttributeError
    # rather than ForbiddenError or AlreadyDecidedError — that's the point of this test.
    with pytest.raises(AttributeError):
        await svc.decide(
            "exap_001",
            DecideInput(decision="approved"),
            identity=_user_identity(),
        )


async def test_decide_denied_does_not_raise_for_user_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """decide() with a user identity and 'denied' passes the guard checks."""
    svc = object.__new__(ExecutionApprovalService)
    with pytest.raises(AttributeError):
        await svc.decide(
            "exap_001",
            DecideInput(decision="denied"),
            identity=_user_identity(),
        )
