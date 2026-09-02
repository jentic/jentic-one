"""Shared-golden contract tests: the mount's execute envelope vs the frozen CLI bytes.

The phase-1 pre-work contract (phase-3 plan §pre-work): golden tool-call
transcripts replayed against both implementations. The Go side pins these in
``cli/internal/cli/api/mcp_golden_test.go``; this is the Python replay against
the same frozen files (``cli/tests/golden/testdata/golden/v2`` — one source of
truth, never a re-pin: drift on either side fails against the identical bytes).

Only the cases where the tool result IS the envelope are shareable (success,
upstream-4xx passthrough); denials deliberately diverge into the soft-error
taxonomy (§3.7) and are asserted field-wise below, mirroring the Go
``mcp_execute_test.go`` coverage of the same golden fixtures.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

import jentic_one.mcp.execute as ex
import jentic_one.mcp.tools as tools_mod
from jentic_one.mcp.tools import CallEnv, dispatch_tool_call
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.config import AuthConfig, ServerConfig
from jentic_one.shared.models import ActorType

_GOLDEN_DIR = (
    Path(__file__).resolve().parents[3] / "cli" / "tests" / "golden" / "testdata" / "golden" / "v2"
)


def shared_golden_stdout(name: str) -> str:
    """The stdout section of one frozen CLI golden (Go: ``sharedGoldenStdout``)."""
    raw = (_GOLDEN_DIR / f"{name}.txt").read_text("utf-8")
    _, _, after = raw.partition("--- stdout ---\n")
    stdout, marker, _ = after.partition("--- stderr ---\n")
    assert marker, f"golden {name} has no stderr marker"
    return stdout


def envelope_without_stamp(payload: dict[str, Any]) -> str:
    """Re-serialize a decoded tool payload minus ``instance`` the way the CLI
    goldens were recorded (Go ``cmdcore.WriteJSON``: 2-space indent, sorted
    keys, trailing newline) so the comparison is byte-exact like for like."""
    assert "instance" in payload, "tool payload carries no instance stamp to strip"
    del payload["instance"]
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def make_env(broker_url: str) -> CallEnv:
    ctx = MagicMock()
    ctx.config.auth = AuthConfig(canonical_base_url="https://auth.example.com")
    server = ServerConfig()
    server.mcp.enabled = True
    server.mcp.broker_url = broker_url
    ctx.config.server = server
    ctx.instance_id = None
    return CallEnv(
        ctx=ctx,
        identity=Identity(sub="agnt_1", permissions=["apis:read"], actor_type=ActorType.AGENT),
        credential="jak_test",
        base_url="https://auth.example.com",
        session_id=None,
    )


@pytest.fixture()
def broker(monkeypatch: pytest.MonkeyPatch):
    """Route the execute path's broker leg into an in-process mock transport.

    Returns a setter taking the httpx.MockTransport handler; requests the
    handler answers carry exactly the headers it sets (plus Content-Length),
    matching the Go httptest mocks the goldens were recorded from.
    """
    state: dict[str, Any] = {}

    def set_handler(handler: Callable[[httpx.Request], httpx.Response]) -> None:
        state["transport"] = httpx.MockTransport(handler)

    def client_factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=state["transport"], follow_redirects=False)

    monkeypatch.setattr(ex, "_broker_client", client_factory)
    return set_handler


def decode_tool_json(result: Any) -> dict[str, Any]:
    (content,) = result.content
    decoded = json.loads(content.text)
    assert isinstance(decoded, dict)
    return decoded


async def test_execute_ok_envelope_matches_the_shared_golden(
    broker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same mock responses TestGolden_ExecuteContract recorded
    execute_ok_json from; resolution is stubbed to the golden's inspect answer
    (the golden pins the ENVELOPE — resolution has its own tests)."""

    async def fake_inspect(env: CallEnv, target: str, revision: str) -> dict[str, Any]:
        assert target == "listPets"
        return {"method": "GET", "url": "https://upstream.example/v1/pets"}

    monkeypatch.setattr(tools_mod, "_inspect_document", fake_inspect)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer jak_test"
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json", "Jentic-Execution-Id": "exec-123"},
            content=b'[{"id":1,"name":"Fido"}]',
        )

    broker(handler)
    env = make_env("http://127.0.0.1:8100")
    result = await dispatch_tool_call(env, "execute", {"operation_id": "listPets"})
    assert not result.is_error, result.content

    got = envelope_without_stamp(decode_tool_json(result))
    assert got == shared_golden_stdout("execute_ok_json")


async def test_execute_upstream_4xx_passthrough_matches_the_shared_golden(broker) -> None:
    """An upstream 4xx relayed by the broker is a normal envelope on both
    surfaces (§3.7 row 1) — Jentic-Error-Origin: upstream means the denial is
    the caller's data, never a soft error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            headers={"Content-Type": "application/json", "Jentic-Error-Origin": "upstream"},
            content=b'{"error":"upstream said no"}',
        )

    broker(handler)
    env = make_env("http://127.0.0.1:8100")
    result = await dispatch_tool_call(env, "execute", {"operation_id": "GET:/v1/pets"})
    assert not result.is_error, result.content

    got = envelope_without_stamp(decode_tool_json(result))
    assert got == shared_golden_stdout("execute_upstream_4xx_passthrough_json")


async def test_execute_payload_is_envelope_plus_stamp_only(broker) -> None:
    """The superset shape itself (§3.7.4): exactly the golden envelope's keys
    plus ``instance``, nothing else — a new sibling key is a contract change
    that must consciously touch both the CLI golden and this pin."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json", "Jentic-Execution-Id": "exec-123"},
            content=b'[{"id":1,"name":"Fido"}]',
        )

    broker(handler)
    env = make_env("http://127.0.0.1:8100")
    result = await dispatch_tool_call(env, "execute", {"operation_id": "GET:/v1/pets"})
    payload = decode_tool_json(result)

    golden_env = json.loads(shared_golden_stdout("execute_ok_json"))
    want = set(golden_env) | {"instance"}
    assert set(payload) == want


async def test_broker_denial_with_directive_is_the_coded_soft_error(broker) -> None:
    """Denials diverge from the CLI rendering into the §3.7 soft-error
    taxonomy; the broker's verbatim agent_directive rides the payload (the
    same fixture the execute_broker_denial_directive_json golden froze)."""
    directive = {
        "instruction": "Ask your operator to approve toolkit acme/pets, then retry.",
        "next_action": "wait_for_approval",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            headers={"Content-Type": "application/json"},
            content=json.dumps({"detail": "denied", "agent_directive": directive}).encode(),
        )

    broker(handler)
    env = make_env("http://127.0.0.1:8100")
    result = await dispatch_tool_call(env, "execute", {"operation_id": "GET:/v1/pets"})
    assert result.is_error

    payload = decode_tool_json(result)
    assert payload["error_code"] == "BROKER_DENIED"
    assert payload["schema_version"] == "1"
    assert payload["agent_directive"] == directive
    assert payload["actionable_step"] == directive["instruction"]
    assert payload["details"] == {"http_status": 403}
    assert payload["retryable"] is False
    assert payload["next_tool"] == "whoami"
    assert "instance" in payload


async def test_insecure_broker_refusal_is_the_coded_transport_error(broker) -> None:
    """SEC-1: a bearer never rides plaintext to a non-loopback broker — the
    same refusal the execute_transport_insecure_broker golden pinned CLI-side."""
    env = make_env("http://broker.internal:8100")
    result = await dispatch_tool_call(env, "execute", {"operation_id": "GET:/v1/pets"})
    assert result.is_error

    payload = decode_tool_json(result)
    assert payload["error_code"] == "TRANSPORT_ERROR"
    assert "plaintext" in payload["error"]


async def test_held_202_envelope_passes_through_untouched(broker) -> None:
    """§3.4: a held execute (202 + job envelope) is a NORMAL tool result — the
    model reads the directive and polls get_execution_result, never re-sends."""
    held_body = {
        "status": "held",
        "job_id": "job_123",
        "agent_directive": {
            "instruction": "The call is held for operator approval. Poll get_execution_result.",
            "next_action": "poll",
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            202,
            headers={"Content-Type": "application/json", "Jentic-Execution-Id": "exec-held"},
            content=json.dumps(held_body).encode(),
        )

    broker(handler)
    env = make_env("http://127.0.0.1:8100")
    result = await dispatch_tool_call(env, "execute", {"operation_id": "POST:/v1/pets"})
    assert not result.is_error, result.content

    payload = decode_tool_json(result)
    assert payload["status"] == 202
    assert payload["body"] == held_body
    assert payload["execution_id"] == "exec-held"


# --- broker-leg transport posture (Go: mcp_execute_test.go's twin coverage) ---


async def test_oversized_streamed_broker_body_fails_closed_at_the_cap(
    broker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MAJOR-2 regression: the broker leg STREAMS the response and stops
    reading at ``_MAX_BODY_BYTES`` (Go: ``ReadAllBounded`` — fail closed,
    never buffer-then-truncate). The chunk counter proves the read stopped at
    the cap rather than draining the multi-'GiB' body first."""
    monkeypatch.setattr(ex, "_MAX_BODY_BYTES", 1 << 10)
    pulled = {"chunks": 0}

    async def chunk_stream() -> AsyncIterator[bytes]:
        for _ in range(1000):
            pulled["chunks"] += 1
            yield b"x" * 256

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"Content-Type": "application/octet-stream"}, content=chunk_stream()
        )

    broker(handler)
    env = make_env("http://127.0.0.1:8100")
    result = await dispatch_tool_call(env, "execute", {"operation_id": "GET:/v1/pets"})
    assert result.is_error

    payload = decode_tool_json(result)
    assert payload["error_code"] == "TRANSPORT_ERROR"
    assert "response body exceeds maximum allowed size" in payload["error"]
    assert pulled["chunks"] < 100, "the read must stop at the cap, not drain the body"


async def test_model_supplied_headers_cannot_override_protected_headers(broker) -> None:
    """MINOR-5 regression: ``Authorization``/``User-Agent`` merge AFTER the
    tool-arg headers — the caller's bearer and the ``jentic-mcp/`` UA (the
    broker's ``Origin.MCP`` signal) always win, in exactly one spelling."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get_list("Authorization")
        seen["user_agent"] = request.headers.get_list("User-Agent")
        seen["x_custom"] = request.headers.get("X-Custom")
        return httpx.Response(200, headers={"Content-Type": "application/json"}, content=b"{}")

    broker(handler)
    env = make_env("http://127.0.0.1:8100")
    result = await dispatch_tool_call(
        env,
        "execute",
        {
            "operation_id": "GET:/v1/pets",
            "headers": {
                "Authorization": "Bearer stolen",
                "authorization": "Bearer stolen-too",
                "User-Agent": "curl/8",
                "X-Custom": "rides",
            },
        },
    )
    assert not result.is_error, result.content
    assert seen["authorization"] == ["Bearer jak_test"]
    (user_agent,) = seen["user_agent"]
    assert user_agent.startswith("jentic-mcp/")
    assert seen["x_custom"] == "rides"


async def test_loopback_prefixed_broker_hostname_is_refused(broker) -> None:
    """MINOR-1 regression: ``127.0.0.1.evil.example`` is a resolvable public
    DNS name — SEC-1 parses the host as an IP (Go ``net.ParseIP`` semantics),
    so a plaintext bearer never rides to it."""
    env = make_env("http://127.0.0.1.evil.example:8100")
    result = await dispatch_tool_call(env, "execute", {"operation_id": "GET:/v1/pets"})
    assert result.is_error

    payload = decode_tool_json(result)
    assert payload["error_code"] == "TRANSPORT_ERROR"
    assert "plaintext" in payload["error"]


async def test_ipv6_loopback_broker_stays_allowed(broker) -> None:
    """The parsed-IP check keeps admitting every literal loopback form."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "application/json"}, content=b"[]")

    broker(handler)
    env = make_env("http://[::1]:8100")
    result = await dispatch_tool_call(env, "execute", {"operation_id": "GET:/v1/pets"})
    assert not result.is_error, result.content
