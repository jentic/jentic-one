"""Web tests for the held executions router: no ``jobs:*`` permission gates.

Visibility and who may act are the service's: a signed-in caller with no
permissions reaches every route, and an unknown id answers ``404`` (never a
permission ``403``). Deciding stays refused for any agent caller (``403``)
whatever its permissions; the jobs routes keep their own gates.
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

_MISSING = "held_web_missing"


def _client(ctx: Context, identity: Identity) -> TestClient:
    app = create_app(ctx)
    app.router.lifespan_context = noop_lifespan

    async def _override(_: object = None) -> Identity:
        return identity

    app.dependency_overrides[resolve_identity] = _override
    return TestClient(app)


def _user(*permissions: str) -> Identity:
    return Identity(sub="usr_web_held", email="held@test.local", permissions=list(permissions))


def _agent(*permissions: str) -> Identity:
    return Identity(
        sub="agnt_web_held",
        permissions=list(permissions),
        actor_type=ActorType.AGENT,
        parent_actor_id="usr_web_held",
    )


@pytest.mark.parametrize("identity", [_user(), _agent()], ids=["user", "agent"])
def test_a_caller_with_no_permissions_reaches_every_read_route(
    web_context: Context, identity: Identity
) -> None:
    with _client(web_context, identity) as tc:
        listed = tc.get("/executions/holds")
        assert listed.status_code == 200
        assert listed.json()["data"] == []
        assert tc.get(f"/executions/holds/{_MISSING}").status_code == 404
        assert tc.get(f"/executions/holds/{_MISSING}/result").status_code == 404
        assert tc.post(f"/executions/holds/{_MISSING}:withdraw").status_code == 404


def test_a_user_with_no_permissions_reaches_decide(web_context: Context) -> None:
    with _client(web_context, _user()) as tc:
        decided = tc.post(f"/executions/holds/{_MISSING}:decide", json={"decision": "deny"})
        assert decided.status_code == 404
        assert decided.json()["type"].endswith("held_execution_not_found")


def test_an_agent_can_never_decide_whatever_its_permissions(web_context: Context) -> None:
    with _client(web_context, _agent("org:admin", "jobs:write")) as tc:
        decided = tc.post(f"/executions/holds/{_MISSING}:decide", json={"decision": "approve"})
        assert decided.status_code == 403


def test_the_jobs_routes_keep_their_permission_gates(web_context: Context) -> None:
    with _client(web_context, _user()) as tc:
        assert tc.get("/jobs").status_code == 403


def test_the_old_approval_routes_are_gone(web_context: Context) -> None:
    with _client(web_context, _user("org:admin")) as tc:
        assert tc.get("/executions/approvals").status_code == 404
