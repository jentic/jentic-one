"""Unit tests for HeldExecutionService guards that run before any DB access.

The decide state machine (compare-and-set, job release/fail, audit, event)
is DB-backed and covered by ``tests/integration/admin/services``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jentic_one.admin.services.errors import HeldExecutionForbiddenError
from jentic_one.admin.services.held_execution_service import HeldExecutionService
from jentic_one.admin.services.schemas.held_executions import DecideInput
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models.actors import ActorType
from jentic_one.shared.models.held_executions import ApprovalDecision


@pytest.mark.asyncio
@pytest.mark.parametrize("permissions", [[], ["org:admin"]])
async def test_decide_refuses_agent_callers_whatever_their_scopes(permissions: list[str]) -> None:
    """An agent never decides — even one granted org:admin — and is refused before any DB call."""
    svc = object.__new__(HeldExecutionService)
    agent = Identity(sub="agnt_1", permissions=permissions, actor_type=ActorType.AGENT)
    with pytest.raises(HeldExecutionForbiddenError):
        await svc.decide("held_001", DecideInput(decision=ApprovalDecision.APPROVE), identity=agent)


def test_decide_input_accepts_only_approve_or_deny() -> None:
    assert DecideInput.model_validate({"decision": "deny"}).decision is ApprovalDecision.DENY
    with pytest.raises(ValidationError):
        DecideInput.model_validate({"decision": "approved"})
