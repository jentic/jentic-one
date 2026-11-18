"""Self-tests for the server-resolution echo router."""

from __future__ import annotations

from httpx import AsyncClient


async def test_resolution_echoes_host_and_path(smoke_client: AsyncClient) -> None:
    response = await smoke_client.get("/servers/resolution")
    assert response.status_code == 200
    body = response.json()
    assert body["host"] == "smoke-corp.local"
    assert body["path"] == "/servers/resolution"
    assert body["resolved"] is True
