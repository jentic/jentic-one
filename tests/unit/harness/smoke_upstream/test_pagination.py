"""Self-tests for the deterministic pagination router."""

from __future__ import annotations

import re

from httpx import AsyncClient

from tests.harness.smoke_upstream.routers.pagination import (
    DEFAULT_PAGE_SIZE,
    HEADER_LINK,
    TOTAL_ITEMS,
)

_NEXT_PAGE_RE = re.compile(r"page=(\d+)>; rel=\"next\"")


async def test_cursor_walks_full_dataset_and_terminates(smoke_client: AsyncClient) -> None:
    collected: list[int] = []
    cursor: str | None = None
    pages = 0
    while True:
        params = {"cursor": cursor} if cursor else {}
        response = await smoke_client.get("/pagination/cursor", params=params)
        assert response.status_code == 200
        body = response.json()
        collected.extend(item["id"] for item in body["data"])
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
        assert pages <= TOTAL_ITEMS

    assert collected == list(range(TOTAL_ITEMS))


async def test_links_emits_next_until_last_page(smoke_client: AsyncClient) -> None:
    page = 1
    seen_pages = 0
    while True:
        response = await smoke_client.get("/pagination/links", params={"page": page})
        assert response.status_code == 200
        seen_pages += 1
        link = response.headers.get(HEADER_LINK)
        if link is None:
            break
        match = _NEXT_PAGE_RE.search(link)
        assert match is not None
        page = int(match.group(1))
        assert seen_pages <= TOTAL_ITEMS

    expected_pages = (TOTAL_ITEMS + DEFAULT_PAGE_SIZE - 1) // DEFAULT_PAGE_SIZE
    assert seen_pages == expected_pages


async def test_offset_slices_and_reports_total(smoke_client: AsyncClient) -> None:
    response = await smoke_client.get("/pagination/offset", params={"limit": 5, "offset": 20})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == TOTAL_ITEMS
    assert [item["id"] for item in body["data"]] == [20, 21, 22, 23, 24]
