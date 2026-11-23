"""Self-tests for the spec-vs-reality drift router."""

from __future__ import annotations

from httpx import AsyncClient


async def test_drift_status_code_returns_418(smoke_client: AsyncClient) -> None:
    response = await smoke_client.get("/drift/status-code")
    assert response.status_code == 418


async def test_drift_content_type_returns_plain_text(smoke_client: AsyncClient) -> None:
    response = await smoke_client.get("/drift/content-type")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")


async def test_drift_schema_returns_unexpected_shape(smoke_client: AsyncClient) -> None:
    response = await smoke_client.get("/drift/schema")
    assert response.status_code == 200
    assert response.json() == {"foo": "bar"}
