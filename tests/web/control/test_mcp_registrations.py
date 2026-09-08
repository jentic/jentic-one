"""Web tests for the MCP config-registration report surface (2-E3, #1189).

Real app, real auth path, real admin database: these cover what the unit
tests deliberately leave out — the actual ``mcp.config_registered`` event row,
the per-(actor, runtime) throttle judged against real writes, error
propagation through a real transaction, and the real 401 for an
unauthenticated call.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from jentic_one.control.services.mcp.service import _registration_throttle
from jentic_one.control.web.app import create_app
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.context import Context
from jentic_one.shared.models import ActorType
from tests.web.conftest import noop_lifespan

from .conftest import _build_app

pytestmark = pytest.mark.integration

MCP_AGENT_SUB = "agnt_webtest_mcp"

MCP_AGENT_IDENTITY = Identity(sub=MCP_AGENT_SUB, actor_type=ActorType.AGENT)


@pytest.fixture()
async def clean_mcp_state(web_context: Context) -> AsyncGenerator[None, None]:
    """Reset the throttle window and this actor's event rows around each test."""

    async def _wipe() -> None:
        _registration_throttle.clear()
        async with web_context.admin_db.session() as session:
            await session.execute(
                text("DELETE FROM events WHERE actor_id = :actor"),
                {"actor": MCP_AGENT_SUB},
            )
            await session.commit()

    await _wipe()
    yield
    await _wipe()


@pytest.fixture()
def mcp_client(web_context: Context, clean_mcp_state: None) -> Iterator[TestClient]:
    app = _build_app(web_context, MCP_AGENT_IDENTITY)
    with TestClient(app) as tc:
        yield tc


@pytest.fixture()
def mcp_unauthed_client(web_context: Context, clean_mcp_state: None) -> Iterator[TestClient]:
    """No identity override — exercises the real auth path (no token → 401)."""
    app = create_app(web_context)
    app.router.lifespan_context = noop_lifespan
    with TestClient(app) as tc:
        yield tc


async def _event_rows(ctx: Context) -> list[dict[str, Any]]:
    async with ctx.admin_db.session() as session:
        result = await session.execute(
            text(
                "SELECT data FROM events "
                "WHERE type = 'mcp.config_registered' AND actor_id = :actor "
                "ORDER BY created_at"
            ),
            {"actor": MCP_AGENT_SUB},
        )
        return [row[0] for row in result]


def test_report_records_event(mcp_client: TestClient, web_context: Context) -> None:
    resp = mcp_client.post("/mcp/config-registrations", json={"runtime": "cursor"})

    assert resp.status_code == 202, resp.text
    assert resp.json() == {"runtime": "cursor", "recorded": True}


@pytest.mark.asyncio
async def test_report_writes_one_event_row(web_context: Context, clean_mcp_state: None) -> None:
    app = _build_app(web_context, MCP_AGENT_IDENTITY)
    with TestClient(app) as client:
        client.post("/mcp/config-registrations", json={"runtime": "claude_desktop"})

    rows = await _event_rows(web_context)
    assert [r["runtime"] for r in rows] == ["claude_desktop"]


@pytest.mark.asyncio
async def test_repeat_report_is_throttled_within_window(
    web_context: Context, clean_mcp_state: None
) -> None:
    """A second identical (actor, runtime) report inside the window is a 202
    with ``recorded: false`` and writes no second row; a DIFFERENT runtime
    from the same actor still records."""
    app = _build_app(web_context, MCP_AGENT_IDENTITY)
    with TestClient(app) as client:
        first = client.post("/mcp/config-registrations", json={"runtime": "cursor"})
        repeat = client.post("/mcp/config-registrations", json={"runtime": "cursor"})
        other = client.post("/mcp/config-registrations", json={"runtime": "codex"})

    assert first.json()["recorded"] is True
    assert repeat.status_code == 202
    assert repeat.json()["recorded"] is False
    assert other.json()["recorded"] is True

    rows = await _event_rows(web_context)
    assert sorted(r["runtime"] for r in rows) == ["codex", "cursor"]


@pytest.mark.asyncio
async def test_emit_failure_propagates_and_frees_the_throttle_slot(
    web_context: Context, clean_mcp_state: None
) -> None:
    """The service does NOT swallow write failures (best-effort lives on the
    CLI side — the 5xx tells it the report was not recorded), and a failed
    write must not burn the throttle slot: the next report records for real."""
    app = _build_app(web_context, MCP_AGENT_IDENTITY)
    with TestClient(app, raise_server_exceptions=False) as client:
        with patch(
            "jentic_one.control.services.mcp.service.emit_event",
            new_callable=AsyncMock,
            side_effect=RuntimeError("db down"),
        ):
            failed = client.post("/mcp/config-registrations", json={"runtime": "cursor"})
        retry = client.post("/mcp/config-registrations", json={"runtime": "cursor"})

    assert failed.status_code == 500
    assert retry.status_code == 202
    assert retry.json()["recorded"] is True
    rows = await _event_rows(web_context)
    assert [r["runtime"] for r in rows] == ["cursor"]


def test_missing_token_returns_401(mcp_unauthed_client: TestClient) -> None:
    resp = mcp_unauthed_client.post("/mcp/config-registrations", json={"runtime": "cursor"})
    assert resp.status_code == 401
