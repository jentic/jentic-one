"""Self-tests for the echo router — request reflection round-trips."""

from __future__ import annotations

import base64

from httpx import AsyncClient

from tests.harness.smoke_upstream.routers.behavior import HEADER_ASYNC_ORIGIN


async def test_echo_reflects_method_path_headers_query_and_text_body(
    smoke_client: AsyncClient,
) -> None:
    response = await smoke_client.post(
        "/behavior/echo",
        params={"q": "search", "page": "2"},
        headers={"X-Custom": "value", "Authorization": "Bearer secret-token"},
        json={"hello": "world"},
    )

    assert response.status_code == 200
    echo = response.json()
    assert echo["method"] == "POST"
    assert echo["url_path"] == "/behavior/echo"
    assert echo["query_params"] == {"q": "search", "page": "2"}
    assert echo["headers"]["x-custom"] == "value"
    assert echo["headers"]["authorization"] == "Bearer secret-token"
    assert echo["body_text"] == '{"hello":"world"}'
    assert echo["body_base64"] is None


async def test_echo_non_utf8_body_lands_in_base64(smoke_client: AsyncClient) -> None:
    payload = bytes([0xFF, 0xFE, 0x00, 0x80])

    response = await smoke_client.post(
        "/behavior/echo",
        content=payload,
        headers={"Content-Type": "application/octet-stream"},
    )

    assert response.status_code == 200
    echo = response.json()
    assert echo["body_text"] is None
    assert echo["body_base64"] == base64.b64encode(payload).decode("ascii")


async def test_echo_empty_body_yields_both_none(smoke_client: AsyncClient) -> None:
    response = await smoke_client.post("/behavior/echo")

    assert response.status_code == 200
    echo = response.json()
    assert echo["body_text"] is None
    assert echo["body_base64"] is None


async def test_upstream_async_returns_202_with_job_id(smoke_client: AsyncClient) -> None:
    response = await smoke_client.post("/behavior/upstream-async")

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"]
    assert body["origin"] == "upstream"
    assert HEADER_ASYNC_ORIGIN not in response.headers


async def test_upstream_async_echoes_async_origin(smoke_client: AsyncClient) -> None:
    response = await smoke_client.post(
        "/behavior/upstream-async",
        headers={HEADER_ASYNC_ORIGIN: "broker"},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["async_origin"] == "broker"
    assert response.headers[HEADER_ASYNC_ORIGIN] == "broker"
