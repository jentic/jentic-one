"""Unit tests for the MCP config-registration report route + service (2-E3, #1189).

The route is the small authenticated endpoint the CLI calls after writing each
runtime's MCP config entry; the service turns the report into the
``mcp.config_registered`` internal event carrying the closed runtime tag.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jentic_one.control.services.mcp.service import McpService
from jentic_one.control.web.deps import get_mcp_service
from jentic_one.control.web.routers.mcp import router
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.models.actors import ActorType
from jentic_one.shared.models.events import EventSeverity, EventType, McpConfigRuntime
from jentic_one.shared.web.deps import resolve_identity


async def _agent_identity() -> Identity:
    return Identity(sub="agnt_test", actor_type=ActorType.AGENT)


def _client(mock_svc: McpService | AsyncMock) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[resolve_identity] = _agent_identity
    app.dependency_overrides[get_mcp_service] = lambda: mock_svc
    return TestClient(app)


# --- Route ---------------------------------------------------------------------


def test_report_returns_202_and_calls_service_with_actor() -> None:
    svc = AsyncMock(spec=McpService)
    client = _client(svc)

    resp = client.post("/mcp/config-registrations", json={"runtime": "cursor"})

    assert resp.status_code == 202
    assert resp.json() == {"runtime": "cursor", "recorded": True}
    svc.report_config_registered.assert_awaited_once_with(
        runtime=McpConfigRuntime.CURSOR,
        actor_id="agnt_test",
        actor_type="agent",
    )


@pytest.mark.parametrize("runtime", [m.value for m in McpConfigRuntime])
def test_report_accepts_every_closed_runtime(runtime: str) -> None:
    svc = AsyncMock(spec=McpService)
    client = _client(svc)

    resp = client.post("/mcp/config-registrations", json={"runtime": runtime})

    assert resp.status_code == 202
    assert resp.json()["runtime"] == runtime


def test_report_rejects_unknown_runtime() -> None:
    """The runtime is a closed enum on the wire — a raw string never validates."""
    svc = AsyncMock(spec=McpService)
    client = _client(svc)

    resp = client.post("/mcp/config-registrations", json={"runtime": "windsurf"})

    assert resp.status_code == 422
    svc.report_config_registered.assert_not_awaited()


# --- Service -------------------------------------------------------------------


def _fake_ctx() -> MagicMock:
    """A Context whose admin_db.transaction() yields an AsyncMock session."""
    ctx = MagicMock()
    session = AsyncMock()
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=session)
    transaction.__aexit__ = AsyncMock(return_value=False)
    ctx.admin_db.transaction.return_value = transaction
    return ctx


@pytest.mark.asyncio
async def test_service_emits_config_registered_with_runtime_tag() -> None:
    svc = McpService(_fake_ctx())

    with patch(
        "jentic_one.control.services.mcp.service.emit_event", new_callable=AsyncMock
    ) as emit:
        await svc.report_config_registered(
            runtime=McpConfigRuntime.CLAUDE_DESKTOP,
            actor_id="agnt_abc",
            actor_type="agent",
        )

    emit.assert_awaited_once()
    kwargs = emit.call_args.kwargs
    assert kwargs["type"] == EventType.MCP_CONFIG_REGISTERED
    assert kwargs["severity"] == EventSeverity.INFO
    assert kwargs["actor_id"] == "agnt_abc"
    assert kwargs["actor_type"] == "agent"
    assert kwargs["data"] == {"runtime": "claude_desktop"}
    # Telemetry side: at most the closed McpConfigRuntime tag, never a raw string.
    assert kwargs["tags"] == {McpConfigRuntime.CLAUDE_DESKTOP}


@pytest.mark.asyncio
async def test_service_propagates_emit_failure() -> None:
    """The service does NOT swallow errors — best-effort lives on the CLI side,
    and the route's 5xx tells the CLI the report was not recorded."""
    svc = McpService(_fake_ctx())

    with (
        patch(
            "jentic_one.control.services.mcp.service.emit_event",
            new_callable=AsyncMock,
            side_effect=RuntimeError("db down"),
        ),
        pytest.raises(RuntimeError),
    ):
        await svc.report_config_registered(
            runtime=McpConfigRuntime.CODEX, actor_id="agnt_abc", actor_type="agent"
        )
