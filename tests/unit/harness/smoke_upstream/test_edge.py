"""Self-tests for the protocol & payload edge-case router."""

from __future__ import annotations

import hashlib

from httpx import AsyncClient

from tests.harness.smoke_upstream.routers.edge import EDGE_CHUNKED_BYTES


async def test_binary_returns_matching_sha256(smoke_client: AsyncClient) -> None:
    payload = bytes(range(256)) * 8
    response = await smoke_client.post(
        "/edge/binary",
        content=payload,
        headers={"Content-Type": "application/octet-stream"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["bytes"] == len(payload)
    assert body["sha256"] == hashlib.sha256(payload).hexdigest()


async def test_empty_204_has_no_content(smoke_client: AsyncClient) -> None:
    response = await smoke_client.get("/edge/empty-204")
    assert response.status_code == 204
    assert response.content == b""


async def test_html_error_is_502_with_html_body(smoke_client: AsyncClient) -> None:
    response = await smoke_client.get("/edge/html-error")
    assert response.status_code == 502
    assert response.headers["content-type"].startswith("text/html")
    assert 4900 <= len(response.text) <= 5200


async def test_chunked_returns_full_payload_size(smoke_client: AsyncClient) -> None:
    response = await smoke_client.post("/edge/chunked")
    assert response.status_code == 200
    assert len(response.content) == EDGE_CHUNKED_BYTES


async def test_form_urlencoded_echoes_parsed_form(smoke_client: AsyncClient) -> None:
    response = await smoke_client.post(
        "/edge/form-urlencoded",
        data={"name": "renton", "role": "admin"},
    )
    assert response.status_code == 200
    assert response.json()["form"] == {"name": "renton", "role": "admin"}


async def test_multipart_echoes_fields_and_files(smoke_client: AsyncClient) -> None:
    file_bytes = b"file-content-bytes"
    response = await smoke_client.post(
        "/edge/multipart",
        data={"description": "an upload"},
        files={"document": ("report.txt", file_bytes, "text/plain")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["fields"] == {"description": "an upload"}
    assert body["files"] == [
        {
            "field": "document",
            "filename": "report.txt",
            "size": len(file_bytes),
            "sha256": hashlib.sha256(file_bytes).hexdigest(),
        }
    ]
