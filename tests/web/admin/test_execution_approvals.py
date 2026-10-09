"""Web tests for the execution approvals router's permission gates.

Each route takes its jobs counterpart's permission before the service applies
reviewer visibility: reads need ``jobs:read``, deciding ``jobs:write``, and
withdrawing ``jobs:read`` (every agent holds it; the service keeps withdraw to
the filer). A caller without the permission is refused at the gate (``403``)
before any row is looked up, so an unknown id answers ``403``, not ``404``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from jentic_one.admin.web.app import create_app
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from jentic_one.shared.web.deps import resolve_identity
from tests.web.conftest import noop_lifespan

pytestmark = pytest.mark.integration

_MISSING = "exap_web_missing"


def _client(ctx: Context, identity: Identity) -> TestClient:
    app = create_app(ctx)
    app.router.lifespan_context = noop_lifespan

    async def _override(_: object = None) -> Identity:
        return identity

    app.dependency_overrides[resolve_identity] = _override
    return TestClient(app)


def _user(*permissions: str) -> Identity:
    return Identity(sub="usr_web_exap", email="exap@test.local", permissions=list(permissions))


def test_a_caller_without_jobs_permissions_is_refused_at_every_route(
    web_context: Context,
) -> None:
    with _client(web_context, _user()) as tc:
        assert tc.get("/executions/approvals").status_code == 403
        assert tc.get(f"/executions/approvals/{_MISSING}").status_code == 403
        decided = tc.post(f"/executions/approvals/{_MISSING}:decide", json={"decision": "approve"})
        assert decided.status_code == 403
        assert tc.post(f"/executions/approvals/{_MISSING}:withdraw").status_code == 403


def test_jobs_read_reads_and_withdraws_but_cannot_decide(web_context: Context) -> None:
    with _client(web_context, _user("jobs:read")) as tc:
        assert tc.get("/executions/approvals").status_code == 200
        assert tc.get(f"/executions/approvals/{_MISSING}").status_code == 404
        assert tc.post(f"/executions/approvals/{_MISSING}:withdraw").status_code == 404
        decided = tc.post(f"/executions/approvals/{_MISSING}:decide", json={"decision": "deny"})
        assert decided.status_code == 403


def test_jobs_write_passes_the_decide_gate(web_context: Context) -> None:
    with _client(web_context, _user("jobs:write")) as tc:
        decided = tc.post(f"/executions/approvals/{_MISSING}:decide", json={"decision": "deny"})
        assert decided.status_code == 404


def test_default_agent_permissions_read_and_withdraw(web_context: Context) -> None:
    agent = Identity(
        sub="agnt_web_exap",
        permissions=["jobs:read", "capabilities:execute"],
        actor_type=ActorType.AGENT,
        parent_actor_id="usr_web_exap",
    )
    with _client(web_context, agent) as tc:
        assert tc.get("/executions/approvals").status_code == 200
        assert tc.post(f"/executions/approvals/{_MISSING}:withdraw").status_code == 404
