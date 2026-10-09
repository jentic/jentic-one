"""Unit tests for held (require-approval) executions on the mount.

The broker leg is an in-process mock transport and the job poll is stubbed:
these pin the per-request front doors — the held result at once for every
client, and the URL elicitation (multi-round-trip) for clients
declaring ``elicitation.url`` — plus the sealed retry state.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock

import httpx
import mcp.types as mcp_types
import pytest
from mcp.shared.exceptions import MCPError

import jentic_one.mcp.execute as ex
import jentic_one.mcp.tools as tools_mod
from jentic_one.mcp import approvals
from jentic_one.mcp.tools import CallEnv, dispatch_mcp_tool_call
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AuthConfig, EncryptionConfig, ServerConfig
from jentic_one.shared.crypto.encryption import EncryptionService
from jentic_one.shared.jobs.hold import HELD_AGENT_DIRECTIVE
from jentic_one.shared.models import ActorType

_KEY = base64.b64encode(b"k" * 32).decode()
_ENVELOPE = {
    "job_id": "job_held1",
    "status": "held",
    "approval": {
        "id": "exap_1",
        "review_url": "https://jentic.example/app/agents/approvals/exap_1",
        "expires_at": "2026-10-07T00:00:00Z",
    },
    "agent_directive": HELD_AGENT_DIRECTIVE,
    "_links": {"self": "https://jentic.example/jobs/job_held1"},
}


_MODERN = "2026-07-28"


def _env(
    caps: dict[str, Any] | None = None,
    *,
    sub: str = "agnt_1",
    protocol_version: str | None = _MODERN,
) -> CallEnv:
    ctx = MagicMock()
    ctx.config.auth = AuthConfig(canonical_base_url="https://auth.example.com")
    server = ServerConfig()
    server.mcp.enabled = True
    server.mcp.broker_url = "http://127.0.0.1:8100"
    ctx.config.server = server
    ctx.instance_id = None
    ctx.encryption = EncryptionService(
        EncryptionConfig.model_validate(
            {"active_id": "v1", "entries": [{"id": "v1", "material": _KEY}]}
        )
    )
    return CallEnv(
        ctx=ctx,
        identity=Identity(sub=sub, permissions=["jobs:read"], actor_type=ActorType.AGENT),
        credential="jak_test",
        base_url="https://auth.example.com",
        session_id=None,
        client_capabilities=caps or {},
        protocol_version=protocol_version,
        client_name="test-client",
        client_version="1.0",
    )


@pytest.fixture()
def held_broker(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """A broker that holds every call (202 + envelope); records what it was sent."""
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(
            202,
            headers={"Content-Type": "application/json", "Jentic-Execution-Id": "exec-9"},
            content=json.dumps(_ENVELOPE).encode(),
        )

    monkeypatch.setattr(
        ex,
        "_broker_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return sent


@pytest.fixture()
def job_polls(monkeypatch: pytest.MonkeyPatch) -> Callable[[list[str]], list[str]]:
    """Stub the job poll with a sequence of statuses and a zero-length wait cadence."""
    seen: list[str] = []

    def install(statuses: list[str]) -> list[str]:
        queue = list(statuses)

        async def poll(env: CallEnv, job_id: str) -> dict[str, Any]:
            seen.append(job_id)
            status = queue.pop(0) if len(queue) > 1 else queue[0]
            payload: dict[str, Any] = {"job_id": job_id, "kind": "execution", "status": status}
            if status == "failed":
                payload["result"] = {"type": "approval_denied", "status": 403}
            return payload

        monkeypatch.setattr(tools_mod, "job_poll_payload", poll)
        return seen

    monkeypatch.setattr(approvals, "SHORT_WAIT_POLL_SECONDS", 0.0)
    return install


def _json(result: Any) -> dict[str, Any]:
    (content,) = result.content
    decoded = json.loads(content.text)
    assert isinstance(decoded, dict)
    return decoded


async def _execute(env: CallEnv) -> Any:
    return await dispatch_mcp_tool_call(
        env,
        "execute",
        {"operation_id": "POST:/v1/charges", "body": {"amount": 5}},
    )


async def test_held_call_without_capabilities_returns_the_held_envelope_at_once(
    held_broker: list[httpx.Request], job_polls: Callable[[list[str]], list[str]]
) -> None:
    polled = job_polls(["held", "queued", "completed"])
    result = await _execute(_env())
    assert isinstance(result, mcp_types.CallToolResult)
    assert not result.is_error
    payload = _json(result)
    assert payload["status"] == 202
    assert payload["body"] == _ENVELOPE
    assert polled == []


async def test_form_only_elicitation_does_not_qualify_for_the_url_door(
    held_broker: list[httpx.Request], job_polls: Callable[[list[str]], list[str]]
) -> None:
    job_polls(["held"])
    result = await _execute(_env({"elicitation": {}}))
    assert isinstance(result, mcp_types.CallToolResult)
    assert _json(result)["body"]["status"] == "held"


async def test_url_elicitation_client_gets_an_input_required_result(
    held_broker: list[httpx.Request], job_polls: Callable[[list[str]], list[str]]
) -> None:
    polled = job_polls(["held"])
    caps: dict[str, Any] = {"elicitation": {"url": {}}}
    result = await _execute(_env(caps))
    assert isinstance(result, mcp_types.InputRequiredResult)
    assert polled == []
    assert result.input_requests is not None
    request = result.input_requests[approvals.REVIEW_INPUT_KEY]
    assert isinstance(request, mcp_types.ElicitRequest)
    assert isinstance(request.params, mcp_types.ElicitRequestURLParams)
    assert request.params.url == "https://jentic.example/app/agents/approvals/exap_1"
    assert "POST /v1/charges" in request.params.message
    assert result.request_state
    assert "job_held1" not in result.request_state


async def test_url_elicitation_retry_reads_the_job_and_never_resends(
    held_broker: list[httpx.Request], job_polls: Callable[[list[str]], list[str]], monkeypatch
) -> None:
    caps: dict[str, Any] = {"elicitation": {"url": {}}}
    env = _env(caps)
    first = await _execute(env)
    assert isinstance(first, mcp_types.InputRequiredResult)
    assert len(held_broker) == 1

    job_polls(["failed"])
    retry = await _execute(replace(env, request_state=first.request_state))
    payload = _json(retry)
    assert payload["status"] == "failed"
    assert payload["result"]["type"] == "approval_denied"
    assert len(held_broker) == 1

    monkeypatch.setattr(approvals, "SHORT_WAIT_SECONDS", 0.0)
    job_polls(["held"])
    still = await _execute(replace(env, request_state=first.request_state))
    held = _json(still)
    assert held["body"]["status"] == "held"
    assert held["body"]["approval"] == _ENVELOPE["approval"]
    assert held["body"]["agent_directive"] == HELD_AGENT_DIRECTIVE
    assert len(held_broker) == 1


async def test_url_elicitation_retry_waits_briefly_for_the_decision(
    held_broker: list[httpx.Request], job_polls: Callable[[list[str]], list[str]]
) -> None:
    """The short wait runs on the retry, after the user was sent to the review page."""
    env = _env({"elicitation": {"url": {}}})
    first = await _execute(env)
    assert isinstance(first, mcp_types.InputRequiredResult)
    polled = job_polls(["held", "queued", "completed"])
    retry = await _execute(replace(env, request_state=first.request_state))
    payload = _json(retry)
    assert payload["status"] == "completed"
    assert len(polled) == 3
    assert len(held_broker) == 1


async def test_forged_or_foreign_request_state_is_refused(
    held_broker: list[httpx.Request], job_polls: Callable[[list[str]], list[str]]
) -> None:
    env = _env({"elicitation": {"url": {}}})
    first = await _execute(env)
    assert isinstance(first, mcp_types.InputRequiredResult)
    for bad in (
        replace(env, request_state="v1:not-a-real-token"),
        replace(_env(sub="agnt_other"), request_state=first.request_state),
    ):
        with pytest.raises(MCPError):
            await _execute(bad)


def test_front_door_selection() -> None:
    url: dict[str, Any] = {"elicitation": {"url": {}}}
    assert approvals.front_door({}, _MODERN) == approvals.FRONT_DOOR_HELD_RESULT
    assert approvals.front_door({"elicitation": {}}, _MODERN) == approvals.FRONT_DOOR_HELD_RESULT
    assert (
        approvals.front_door({"elicitation": {"form": {}}}, _MODERN)
        == approvals.FRONT_DOOR_HELD_RESULT
    )
    assert approvals.front_door(url, _MODERN) == approvals.FRONT_DOOR_URL_ELICITATION
    assert (
        approvals.front_door({"extensions": {"io.modelcontextprotocol/tasks": {}}}, _MODERN)
        == approvals.FRONT_DOOR_HELD_RESULT
    )
    # An InputRequiredResult cannot ride an older (or unknown) protocol version.
    assert approvals.front_door(url, "2025-06-18") == approvals.FRONT_DOOR_HELD_RESULT
    assert approvals.front_door(url, None) == approvals.FRONT_DOOR_HELD_RESULT


async def test_a_legacy_wire_client_declaring_url_elicitation_gets_the_held_result(
    held_broker: list[httpx.Request], job_polls: Callable[[list[str]], list[str]]
) -> None:
    job_polls(["held"])
    result = await _execute(_env({"elicitation": {"url": {}}}, protocol_version="2025-06-18"))
    assert isinstance(result, mcp_types.CallToolResult)
    assert _json(result)["body"]["status"] == "held"


async def _get_result(env: CallEnv, **extra: Any) -> Any:
    return await dispatch_mcp_tool_call(
        env, "get_execution_result", {"job_id": "job_held1", **extra}
    )


async def test_get_execution_result_without_wait_polls_once(
    job_polls: Callable[[list[str]], list[str]],
) -> None:
    polled = job_polls(["held", "completed"])
    payload = _json(await _get_result(_env()))
    assert payload["status"] == "held"
    assert polled == ["job_held1"]


async def test_get_execution_result_wait_returns_early_once_terminal(
    job_polls: Callable[[list[str]], list[str]],
) -> None:
    polled = job_polls(["held", "queued", "completed"])
    payload = _json(await _get_result(_env(), wait_seconds=30))
    assert payload["status"] == "completed"
    assert len(polled) == 3


async def test_get_execution_result_negative_wait_answers_at_once(
    job_polls: Callable[[list[str]], list[str]],
) -> None:
    polled = job_polls(["held"])
    payload = _json(await _get_result(_env(), wait_seconds=-5))
    assert payload["status"] == "held"
    assert polled == ["job_held1"]
