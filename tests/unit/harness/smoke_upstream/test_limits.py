"""Self-tests for the query-array limits router."""

from __future__ import annotations

from httpx import AsyncClient


async def test_query_array_captures_all_repeated_ids(smoke_client: AsyncClient) -> None:
    ids = [str(value) for value in range(50)]
    response = await smoke_client.get(
        "/limits/query-array",
        params=[("ids", value) for value in ids],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == len(ids)
    assert body["ids"] == ids
