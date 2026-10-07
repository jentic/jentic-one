"""Unit tests for the MCP config-registration report route (2-E3, #1189).

The route is the small authenticated endpoint the CLI calls after writing each
runtime's MCP config entry. Route tests mock the SERVICE (the layer boundary);
everything DB-backed — the emitted ``mcp.config_registered`` event, the
throttle against real rows, the real unauthenticated 401 — lives in the
web-tier tests (``tests/web/control/test_mcp_registrations.py``) against real
fixtures. The in-process throttle window itself is pure and unit-tested here.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.control.services.mcp.service import McpService, _ThrottleWindow
from jentic_one.control.web.deps import get_mcp_service
from jentic_one.control.web.routers.mcp import router
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models.actors import ActorType
from jentic_one.shared.models.events import McpConfigRuntime
from jentic_one.shared.web.deps import resolve_identity


async def _agent_identity() -> Identity:
    return Identity(sub="agnt_test", actor_type=ActorType.AGENT)


def _client(mock_svc: McpService | AsyncMock) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[resolve_identity] = _agent_identity
    app.dependency_overrides[get_mcp_service] = lambda: mock_svc
    return TestClient(app)


def _service_mock(*, recorded: bool = True) -> AsyncMock:
    svc = AsyncMock(spec=McpService)
    svc.report_config_registered.return_value = recorded
    return svc


# --- Route ---------------------------------------------------------------------


def test_report_returns_202_and_calls_service_with_actor() -> None:
    svc = _service_mock()
    client = _client(svc)

    resp = client.post("/mcp/config-registrations", json={"runtime": "cursor"})

    assert resp.status_code == 202
    assert resp.json() == {"runtime": "cursor", "recorded": True}
    svc.report_config_registered.assert_awaited_once_with(
        runtime=McpConfigRuntime.CURSOR,
        actor_id="agnt_test",
        actor_type="agent",
    )


def test_report_surfaces_throttled_as_recorded_false() -> None:
    """A throttled repeat is still a 202 — the CLI's report is fire-and-forget —
    but ``recorded`` must reflect that nothing was written."""
    client = _client(_service_mock(recorded=False))

    resp = client.post("/mcp/config-registrations", json={"runtime": "cursor"})

    assert resp.status_code == 202
    assert resp.json() == {"runtime": "cursor", "recorded": False}


@pytest.mark.parametrize("runtime", [m.value for m in McpConfigRuntime])
def test_report_accepts_every_closed_runtime(runtime: str) -> None:
    client = _client(_service_mock())

    resp = client.post("/mcp/config-registrations", json={"runtime": runtime})

    assert resp.status_code == 202
    assert resp.json()["runtime"] == runtime


def test_report_rejects_unknown_runtime() -> None:
    """The runtime is a closed enum on the wire — a raw string never validates."""
    svc = _service_mock()
    client = _client(svc)

    resp = client.post("/mcp/config-registrations", json={"runtime": "windsurf"})

    assert resp.status_code == 422
    svc.report_config_registered.assert_not_awaited()


# --- Throttle window (pure, no I/O) ---------------------------------------------


def test_throttle_window_accepts_once_per_key() -> None:
    w = _ThrottleWindow(window_seconds=3600)
    assert w.try_accept(("agnt_a", "cursor")) is True
    assert w.try_accept(("agnt_a", "cursor")) is False
    # Different runtime and different actor each get their own slot.
    assert w.try_accept(("agnt_a", "codex")) is True
    assert w.try_accept(("agnt_b", "cursor")) is True


def test_throttle_window_expires() -> None:
    w = _ThrottleWindow(window_seconds=0.0)  # everything expires immediately
    assert w.try_accept(("agnt_a", "cursor")) is True
    assert w.try_accept(("agnt_a", "cursor")) is True


def test_throttle_window_release_frees_the_slot() -> None:
    """A failed event write releases its slot so the next report can record."""
    w = _ThrottleWindow(window_seconds=3600)
    key = ("agnt_a", "cursor")
    assert w.try_accept(key) is True
    w.release(key)
    assert w.try_accept(key) is True
