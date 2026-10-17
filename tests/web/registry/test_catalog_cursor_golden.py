"""Golden bytes for ``GET /catalog`` paging under the default (token) ranking.

Pins, byte for byte, every page of four walks over a fixed manifest: the
response entries, ``has_more`` and each page's ``next_cursor``. Cursors are
stored base64-decoded (``next_cursor_json``) so the fixture is reviewable and
reads as data rather than opaque tokens; base64 is a bijection, so this still
pins the exact cursor bytes. The fixture
(``tests/fixtures/catalog_cursor_golden.json``) was captured before the catalog
ranking seam existed (#1833), so a default install's responses and cursors must
stay identical to it. Never regenerate it to make this test pass; a diff here
means existing clients' cursors or response bytes changed.

Walks (``limit=2``):

- ``token``: a ranked search (``q="pay api"``), three or more pages;
- ``browse``: no ``q``;
- ``no_tokens``: ``q="---"``, which has no search tokens and must equal browse;
- ``filtered``: the ranked search with ``registered_only=true``.
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from jentic_one.shared.context import Context
from tests.web.registry.conftest import (
    delete_registered_apis,
    register_catalog_entries,
    seed_catalog_snapshot,
)

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "catalog_cursor_golden.json"

#: Deliberately unsorted so the walk must impose its own order.
_MANIFEST = [
    "zapi.com",
    "pay.com",
    "unrelated.org",
    "payments-api.net",
    "pay-api.com",
    "stripe.com",
    "openpay.dev",
    "api.pay.io",
    "paypal.com",
    "apiary.io",
    "weather.gov",
]
_REGISTERED = ["pay.com", "pay-api.com", "api.pay.io", "openpay.dev", "zapi.com"]

_WALKS: dict[str, dict[str, str | int | bool]] = {
    "token": {"q": "pay api", "limit": 2},
    "browse": {"limit": 2},
    "no_tokens": {"q": "---", "limit": 2},
    "filtered": {"q": "pay api", "registered_only": True, "limit": 2},
}


@pytest.fixture()
async def golden_catalog(web_context: Context, clean_catalog: None) -> AsyncGenerator[None, None]:
    await seed_catalog_snapshot(web_context, _MANIFEST)
    await register_catalog_entries(web_context, _REGISTERED)
    try:
        yield
    finally:
        await delete_registered_apis(web_context)


def _walk(client: TestClient, params: dict[str, str | int | bool]) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(100):
        query: dict[str, str | int | bool] = {**params}
        if cursor is not None:
            query["cursor"] = cursor
        resp = client.get("/catalog", params=query)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        raw_cursor = body["next_cursor"]
        pages.append(
            {
                "data": body["data"],
                "has_more": body["has_more"],
                "next_cursor_json": (
                    None if raw_cursor is None else base64.b64decode(raw_cursor).decode()
                ),
            }
        )
        if not body["has_more"]:
            return pages
        cursor = body["next_cursor"]
    raise AssertionError("pagination did not terminate")


def test_catalog_cursor_bytes_match_golden(admin_client: TestClient, golden_catalog: None) -> None:
    """Every page of the four default-install walks is byte-identical to the fixture."""
    actual = {name: _walk(admin_client, params) for name, params in _WALKS.items()}

    expected = json.loads(_FIXTURE.read_text())
    assert actual == expected
    # The fixture itself is the contract; sanity-check it covers what it claims.
    assert len(expected["token"]) >= 3
    assert len(expected["browse"]) >= 3
    assert expected["no_tokens"] == expected["browse"]
    assert all("match" not in e for walk in expected.values() for p in walk for e in p["data"])
