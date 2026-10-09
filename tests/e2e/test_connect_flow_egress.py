"""A key connected through the agent connect flow obeys the broker's egress policy.

Pinning a session to a spec's hosts is not an egress exemption: an agent can
ask to connect an API whose ``servers`` name a private address, a human can
approve it, and the broker must still refuse to send the key there unless the
operator allowlisted the range. The stack allowlists ``127.0.0.0/8`` only.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from tests.e2e.conftest import (
    E2E_VENDOR,
    Stack,
    StackAgent,
    import_inline,
    make_agent,
    recorded_requests,
    spec,
)
from tests.smoke.conftest import authed_request, broker_call

pytestmark = pytest.mark.e2e

_CANARY = "canary-egress-" + uuid.uuid4().hex  # pragma: allowlist secret
_RULES = [{"effect": "allow", "methods": ["GET"], "path": ".*", "match_mode": "regex"}]


def _connect_through_the_flow(
    stack: Stack, admin_token: str, agent: StackAgent, *, server: str
) -> dict[str, str]:
    """Import an API on ``server``; the agent asks, the admin approves with a pasted key."""
    name, version = f"egress-{uuid.uuid4().hex[:8]}", "1.0.0"
    revision = import_inline(
        stack,
        admin_token,
        vendor=E2E_VENDOR,
        name=name,
        version=version,
        content=spec(title="Egress", version=version, server=server, path="/items"),
    )
    _, status = authed_request(
        f"{stack.app_url}/apis/{E2E_VENDOR}/{name}/{version}/revisions/"
        f"{revision['revision_id']}:promote",
        method="POST",
        token=admin_token,
    )
    assert status == 200
    asked, status = authed_request(
        f"{stack.app_url}/integrations:connect",
        method="POST",
        token=agent.token,
        body={"api": {"vendor": E2E_VENDOR, "name": name, "version": version}},
    )
    assert status == 201 and isinstance(asked, dict), (status, asked)
    session_id = asked["session_id"]
    review, status = authed_request(
        f"{stack.app_url}/connect-sessions/{session_id}", token=admin_token
    )
    assert status == 200 and isinstance(review, dict), (status, review)
    confirmed, status = authed_request(
        f"{stack.app_url}/connect-sessions/{session_id}:confirm",
        method="POST",
        token=admin_token,
        body={
            "kind": "api_key",
            "key": _CANARY,
            "permission_rules": _RULES,
            "expected_agent_id": agent.agent_id,
            "digest": review["digest"],
        },
    )
    assert status == 200 and isinstance(confirmed, dict), (status, confirmed)
    assert confirmed["kind"] == "connected"
    return {"name": name, "version": version, "pinned": review["pinned_hosts"]}


@pytest.fixture(scope="module")
def agent(stack: Stack, admin_token: str) -> StackAgent:
    return make_agent(stack, admin_token, extra_permissions=[])


def test_allowlisted_host_receives_the_connected_key(
    recorder: Stack, admin_token: str, agent: StackAgent
) -> None:
    stack = recorder
    _connect_through_the_flow(stack, admin_token, agent, server=stack.recorder_url)
    body, status, _ = broker_call(
        stack.broker_url, f"{stack.recorder_url}/items", token=agent.token
    )
    assert status < 400, body
    assert [r for r in recorded_requests(stack) if _CANARY in str(r)], "key never reached host"


@pytest.mark.parametrize(
    "server",
    [
        "http://10.255.255.1:8080",
        "http://192.168.255.1",
        "http://169.254.169.254",
        "http://[fd00::1]",
    ],
)
def test_private_host_is_refused_even_after_approval(
    recorder: Stack, admin_token: str, agent: StackAgent, server: str
) -> None:
    stack = recorder
    connected: dict[str, Any] = _connect_through_the_flow(stack, admin_token, agent, server=server)
    assert connected["pinned"], connected
    body, status, _ = broker_call(stack.broker_url, f"{server}/items", token=agent.token)
    assert status in (400, 403), (status, body)
    assert b"invalid_upstream_url" in body, body
    assert _CANARY.encode() not in body
