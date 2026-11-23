"""Self-tests for the parameter-serialization echo router."""

from __future__ import annotations

from httpx import AsyncClient

from tests.harness.smoke_upstream.routers.parameters import HEADER_ARRAY


async def test_matrix_params_parsed(smoke_client: AsyncClient) -> None:
    response = await smoke_client.get("/parameters/query/matrix/;color=blue;size=large")
    assert response.status_code == 200
    assert response.json()["parsed"] == {"color": "blue", "size": "large"}


async def test_pipe_delimited_array(smoke_client: AsyncClient) -> None:
    response = await smoke_client.get("/parameters/query/pipe", params={"array": "1|2|3"})
    assert response.status_code == 200
    assert response.json()["array"] == ["1", "2", "3"]


async def test_deep_object(smoke_client: AsyncClient) -> None:
    response = await smoke_client.get(
        "/parameters/query/deep-object",
        params={"user[name]": "renton", "user[role]": "admin"},
    )
    assert response.status_code == 200
    assert response.json()["user"] == {"name": "renton", "role": "admin"}


async def test_header_array(smoke_client: AsyncClient) -> None:
    response = await smoke_client.get("/parameters/header/array", headers={HEADER_ARRAY: "a, b ,c"})
    assert response.status_code == 200
    assert response.json()["values"] == ["a", "b", "c"]
