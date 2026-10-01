"""Unit tests for the catalog logo route's HTTP contract.

The pure-HTTP behaviours — locked-down response headers, the content-derived
``ETag`` and ``If-None-Match`` → ``304``, and the 404 problem types — run here
against a stubbed service. Fetching and caching are covered by the web suite.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jentic.problem_details import ProblemDetailException, problem_detail_exception_handler

from jentic_one.registry.services.catalog.service import CatalogLogoView
from jentic_one.registry.services.errors import (
    CatalogEntryNotFoundError,
    CatalogLogoNotFoundError,
)
from jentic_one.registry.web.app import get_exception_handlers
from jentic_one.registry.web.deps import get_catalog_service
from jentic_one.registry.web.routers import catalog
from jentic_one.shared.auth.identity import Identity
from jentic_one.shared.web.deps import resolve_identity

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
_DIGEST = hashlib.sha256(_PNG).hexdigest()


class _StubService:
    async def logo(self, api_id: str) -> CatalogLogoView:
        if api_id == "googleapis.com/admin":
            return CatalogLogoView(content=_PNG, content_type="image/png", digest=_DIGEST)
        if api_id == "slack.com":
            raise CatalogLogoNotFoundError(api_id)
        raise CatalogEntryNotFoundError(api_id)


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]
    for exc_class, handler in get_exception_handlers():
        app.add_exception_handler(exc_class, handler)
    app.include_router(catalog.router)
    identity = Identity(sub="usr_test", permissions=["capabilities:read"])
    app.dependency_overrides[resolve_identity] = lambda: identity
    app.dependency_overrides[get_catalog_service] = lambda: _StubService()
    return TestClient(app, headers={"Authorization": "Bearer test-token"})


def test_logo_served_with_locked_down_headers(client: TestClient) -> None:
    # A slash-bearing api_id resolves to the logo route, not the bare entry route.
    resp = client.get("/catalog/googleapis.com/admin/logo")
    assert resp.status_code == 200
    assert resp.content == _PNG
    assert resp.headers["Content-Type"] == "image/png"
    assert resp.headers["ETag"] == f'"{_DIGEST}"'
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Content-Security-Policy"] == "default-src 'none'; sandbox"
    assert resp.headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert resp.headers["Cache-Control"] == "private, max-age=86400"


@pytest.mark.parametrize("if_none_match", [f'"{_DIGEST}"', f'W/"{_DIGEST}"', "*"])
def test_matching_if_none_match_yields_empty_304(client: TestClient, if_none_match: str) -> None:
    resp = client.get(
        "/catalog/googleapis.com/admin/logo", headers={"If-None-Match": if_none_match}
    )
    assert resp.status_code == 304
    assert resp.content == b""
    assert resp.headers["ETag"] == f'"{_DIGEST}"'


def test_non_matching_if_none_match_yields_full_200(client: TestClient) -> None:
    resp = client.get("/catalog/googleapis.com/admin/logo", headers={"If-None-Match": '"old"'})
    assert resp.status_code == 200
    assert resp.content == _PNG


@pytest.mark.parametrize(
    ("api_id", "problem_type"),
    [("slack.com", "catalog_logo_not_found"), ("nope.com", "catalog_entry_not_found")],
)
def test_missing_logo_and_unknown_entry_are_404(
    client: TestClient, api_id: str, problem_type: str
) -> None:
    resp = client.get(f"/catalog/{api_id}/logo")
    assert resp.status_code == 404
    assert problem_type in json.dumps(resp.json())


def test_openapi_declares_image_content_and_304() -> None:
    app = FastAPI()
    app.include_router(catalog.router)
    op = app.openapi()["paths"]["/catalog/{api_id}/logo"]["get"]
    assert set(op["responses"]["200"]["content"]) == {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
    }
    assert "304" in op["responses"]
    assert "404" in op["responses"]
