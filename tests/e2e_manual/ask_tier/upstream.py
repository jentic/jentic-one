"""The ask-tier e2e upstream: the smoke-upstream harness plus a few recorded routes.

``tests.harness.smoke_upstream`` is the base app (its ``/behavior/echo`` reflects
method, headers and body). On top of it this module serves:

- ``GET /specs/ask.json`` — the OpenAPI document the e2e imports, rooted at
  this process (``ASK_UPSTREAM_URL``) and secured with a bearer scheme, so the
  broker injects the bound credential (``/specs/ask-two.json``: the same, on
  ``localhost``, for a second vendor);
- ``GET /items`` — lists, echoing its query string;
- ``POST /orders`` — echoes the exact body bytes and the request headers;
- ``POST /slow`` — sleeps ``?seconds=`` before answering (the worker-crash
  scenario kills the worker while it waits);
- ``GET /_calls`` / ``POST /_reset`` — every call above, recorded in arrival
  order, so a scenario can prove a held call ran exactly once (or never).

Test-only. Run as ``python -m tests.e2e_manual.ask_tier.upstream``.
"""

from __future__ import annotations

import asyncio
import base64
import os
from typing import Any

import uvicorn
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from tests.harness.smoke_upstream.app import build_smoke_app

_CALLS: list[dict[str, Any]] = []
router = APIRouter()


def _base_url() -> str:
    return os.environ.get("ASK_UPSTREAM_URL", "http://127.0.0.1:55523").rstrip("/")


async def _record(request: Request) -> dict[str, Any]:
    raw = await request.body()
    entry = {
        "method": request.method,
        "path": request.url.path,
        "query": request.url.query,
        "headers": {k.lower(): v for k, v in request.headers.items()},
        "body_b64": base64.b64encode(raw).decode() if raw else None,
    }
    _CALLS.append(entry)
    return entry


@router.get("/items")
async def list_items(request: Request) -> dict[str, Any]:
    entry = await _record(request)
    return {"items": [{"id": 1}, {"id": 2}], "query": entry["query"]}


@router.post("/orders")
async def create_order(request: Request) -> JSONResponse:
    entry = await _record(request)
    return JSONResponse(
        status_code=201,
        content={
            "created": True,
            "content_type": entry["headers"].get("content-type"),
            "body_b64": entry["body_b64"],
            "authorization": entry["headers"].get("authorization"),
        },
    )


@router.post("/slow")
async def slow(request: Request, seconds: float = 5.0) -> dict[str, Any]:
    await _record(request)
    await asyncio.sleep(seconds)
    return {"slept": seconds}


@router.get("/_calls")
async def calls() -> list[dict[str, Any]]:
    return _CALLS


@router.post("/_reset")
async def reset() -> dict[str, int]:
    n = len(_CALLS)
    _CALLS.clear()
    return {"cleared": n}


def _op(op_id: str, summary: str, *, params: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    op: dict[str, Any] = {
        "operationId": op_id,
        "summary": summary,
        "description": summary,
        "security": [{"bearerAuth": []}],
        "responses": {"200": {"description": "ok"}},
    }
    if params:
        op["parameters"] = params
    return op


@router.get("/specs/ask.json")
async def spec() -> dict[str, Any]:
    return _spec(_base_url())


@router.get("/specs/ask-two.json")
async def spec_two() -> dict[str, Any]:
    """The same document rooted at ``localhost``: a second host, so a second vendor
    can import it (a host is served by one vendor at a time)."""
    return _spec(_base_url().replace("127.0.0.1", "localhost"))


def _spec(server_url: str) -> dict[str, Any]:
    body = {
        "content": {"application/json": {"schema": {"type": "object"}}},
        "required": True,
    }
    create = _op("createOrder", "Create an order")
    create["requestBody"] = body
    slow_op = _op(
        "slowOp",
        "Slow operation",
        params=[{"name": "seconds", "in": "query", "schema": {"type": "number"}}],
    )
    echo = _op("echo", "Echo the request")
    echo["requestBody"] = body
    return {
        "openapi": "3.1.0",
        "info": {"title": "Ask Tier E2E", "version": "1.0.0"},
        "servers": [{"url": server_url}],
        "components": {"securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}},
        "paths": {
            "/items": {
                "get": _op(
                    "listItems",
                    "List items",
                    params=[{"name": "limit", "in": "query", "schema": {"type": "integer"}}],
                )
            },
            "/orders": {"post": create},
            "/slow": {"post": slow_op},
            "/behavior/echo": {"post": echo},
        },
    }


def build_app() -> FastAPI:
    app = build_smoke_app()
    app.include_router(router)
    return app


def main() -> None:
    uvicorn.run(build_app(), host="127.0.0.1", port=int(os.environ.get("PORT", "55523")))


if __name__ == "__main__":
    main()
