"""Regression test: OTel FastAPI route details survive partial route matches.

FastAPI >= 0.137 wraps ``include_router`` results in an opaque
``_IncludedRouter`` with no ``path``. On ``opentelemetry-instrumentation-fastapi``
<= 0.63b1 a request that path-matched an included router without matching a
method (a CORS ``OPTIONS`` preflight, a ``405``) hit an unguarded
``route.path`` read in ``_get_route_details``, raising ``AttributeError`` and
turning the response into a ``500``. jentic-one used to monkeypatch a local
guard around ``_get_route_details``; upstream fixed the lookup properly in
0.65b0 (``_flatten_routes`` over FastAPI's ``iter_route_contexts``), so the
guard was removed and this test now pins the upstream behaviour instead —
``pyproject.toml`` floors the instrumentation at the fixed version.
"""

from __future__ import annotations

from typing import Any

import opentelemetry.instrumentation.fastapi as otel_fastapi
from fastapi import APIRouter, FastAPI
from starlette.testclient import TestClient


def _make_app_with_included_router() -> FastAPI:
    app = FastAPI()
    router = APIRouter()

    @router.post("/auth/login")
    def login() -> dict[str, bool]:  # pragma: no cover - never invoked
        return {"ok": True}

    app.include_router(router)
    return app


def test_route_details_survive_partial_match() -> None:
    """A method-mismatched scope resolves the templated path, not an error."""
    app = _make_app_with_included_router()
    scope = {"type": "http", "method": "OPTIONS", "path": "/auth/login", "app": app}
    get_details: Any = otel_fastapi._get_route_details
    # On otel-instrumentation-fastapi <= 0.63b1 this raised AttributeError.
    assert get_details(scope) == "/auth/login"


def test_method_mismatch_request_does_not_500() -> None:
    """An instrumented app answers a 405 as a 405, not a 500."""
    app = _make_app_with_included_router()
    otel_fastapi.FastAPIInstrumentor.instrument_app(app)
    try:
        client = TestClient(app, raise_server_exceptions=True)
        assert client.options("/auth/login").status_code == 405
        assert client.post("/auth/login").status_code == 200
    finally:
        otel_fastapi.FastAPIInstrumentor.uninstrument_app(app)
