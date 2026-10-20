"""Tests for :class:`AuthenticatedResponseCacheMiddleware` (#1835).

Pins that a JSON or problem+json response to a credentialed request carries
``Cache-Control: private, no-store`` so a shared cache cannot serve one
caller's body to another, while anonymous requests, non-JSON responses and a
route's own ``Cache-Control`` are left alone.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.testclient import TestClient
from jentic.problem_details import (
    Forbidden,
    ProblemDetailException,
    problem_detail_exception_handler,
)

from jentic_one.shared.web.auth import API_KEY_HEADER
from jentic_one.shared.web.cache_control import (
    PRIVATE_NO_STORE,
    AuthenticatedResponseCacheMiddleware,
    request_carries_credential,
)

_BEARER = {"Authorization": "Bearer tok"}
_API_KEY = {API_KEY_HEADER: "key"}  # pragma: allowlist secret


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    asset = tmp_path / "file.json"
    asset.write_text("{}", encoding="utf-8")

    app = FastAPI()

    @app.get("/json")
    def _json() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/json-charset")
    def _json_charset() -> Response:
        return Response(b"{}", media_type="application/json; charset=utf-8")

    @app.get("/problem")
    def _problem() -> None:
        raise Forbidden(detail="nope", instance="/problem", type="forbidden")

    @app.get("/html")
    def _html() -> HTMLResponse:
        return HTMLResponse("<p>hi</p>")

    @app.get("/sse")
    def _sse() -> StreamingResponse:
        async def _events() -> AsyncIterator[bytes]:
            yield b"data: 1\n\n"

        return StreamingResponse(_events(), media_type="text/event-stream")

    @app.get("/file")
    def _file() -> FileResponse:
        return FileResponse(asset, media_type="application/octet-stream")

    @app.get("/route-no-cache")
    def _route_no_cache() -> JSONResponse:
        return JSONResponse({"ok": "yes"}, headers={"Cache-Control": "no-cache"})

    @app.get("/route-no-store-lower")
    def _route_no_store_lower() -> Response:
        response = Response(b"{}", media_type="application/json")
        response.raw_headers.append((b"CACHE-CONTROL", b"no-store"))
        return response

    app.add_exception_handler(ProblemDetailException, problem_detail_exception_handler)  # type: ignore[arg-type]
    app.add_middleware(AuthenticatedResponseCacheMiddleware)
    return TestClient(app)


def test_bearer_json_is_private_no_store(client: TestClient) -> None:
    resp = client.get("/json", headers=_BEARER)
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == PRIVATE_NO_STORE


def test_api_key_problem_json_is_private_no_store(client: TestClient) -> None:
    resp = client.get("/problem", headers=_API_KEY)
    assert resp.status_code == 403
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.headers["cache-control"] == PRIVATE_NO_STORE


def test_json_with_charset_parameter_is_matched(client: TestClient) -> None:
    resp = client.get("/json-charset", headers=_BEARER)
    assert resp.headers["cache-control"] == PRIVATE_NO_STORE


@pytest.mark.parametrize("headers", [{}, {"Authorization": ""}, {API_KEY_HEADER: "  "}])
def test_anonymous_json_is_untouched(client: TestClient, headers: dict[str, str]) -> None:
    resp = client.get("/json", headers=headers)
    assert resp.status_code == 200
    assert "cache-control" not in resp.headers


@pytest.mark.parametrize("path", ["/html", "/sse", "/file"])
def test_non_json_responses_are_untouched(client: TestClient, path: str) -> None:
    resp = client.get(path, headers=_BEARER)
    assert resp.status_code == 200
    assert "cache-control" not in resp.headers


def test_route_cache_control_is_never_overridden(client: TestClient) -> None:
    resp = client.get("/route-no-cache", headers=_BEARER)
    assert resp.headers.get_list("cache-control") == ["no-cache"]


def test_route_cache_control_header_name_is_case_insensitive(client: TestClient) -> None:
    resp = client.get("/route-no-store-lower", headers=_BEARER)
    assert resp.headers.get_list("cache-control") == ["no-store"]


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ([(b"authorization", b"Bearer tok")], True),
        ([(b"Authorization", b"Bearer tok")], True),
        ([(API_KEY_HEADER.encode(), b"key")], True),
        ([(b"authorization", b"   ")], False),
        ([(b"cookie", b"session=1")], False),
        ([], False),
    ],
)
def test_request_carries_credential(headers: list[tuple[bytes, bytes]], expected: bool) -> None:
    assert request_carries_credential(headers) is expected
