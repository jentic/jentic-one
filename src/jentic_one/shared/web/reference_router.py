"""Public ``GET /reference/endpoints.json`` — the canonical endpoint reference.

Serves the same payload as the committed ``docs/reference/endpoints.json`` (both
call :func:`jentic_one.shared.web.endpoint_reference.build_reference_payload`), so
the CLI and the docs SPA have one machine-readable source of truth without
parsing vendor extensions out of the OpenAPI document.

Hidden from the OpenAPI schema: it is tooling metadata, not a product API, and
keeping it out of the spec keeps the spec clean.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from jentic_one.shared.web.endpoint_reference import (
    build_reference_payload,
    collect_endpoints,
)

REFERENCE_PATH = "/reference/endpoints.json"


def _reference_payload(app: Any) -> dict[str, Any]:
    """Build (and cache on app.state) the reference payload for this app.

    The join introspects routes + the curated permission map, which is stable for the
    lifetime of the process, so it is computed once and cached.
    """
    cached = getattr(app.state, "endpoint_reference_payload", None)
    if cached is not None:
        return dict(cached)
    endpoints = collect_endpoints(app)
    payload = build_reference_payload(endpoints)
    app.state.endpoint_reference_payload = payload
    return payload


def get_reference_router() -> APIRouter:
    """Router exposing the public, schema-hidden endpoint reference."""
    router = APIRouter()

    @router.get(REFERENCE_PATH, include_in_schema=False)
    async def endpoints_reference(request: Request) -> JSONResponse:
        return JSONResponse(_reference_payload(request.app))

    return router
