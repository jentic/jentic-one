"""Self-tests for the deprecation / lifecycle-header router."""

from __future__ import annotations

from httpx import AsyncClient

from tests.harness.smoke_upstream.routers.lifecycle import (
    HEADER_DEPRECATION,
    HEADER_SUNSET,
)


async def test_deprecated_endpoint_emits_lifecycle_headers(smoke_client: AsyncClient) -> None:
    response = await smoke_client.get("/lifecycle/deprecated-endpoint")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert response.headers[HEADER_DEPRECATION] == "true"
    assert response.headers[HEADER_SUNSET]
    assert "warning" not in {key.lower() for key in response.headers}
