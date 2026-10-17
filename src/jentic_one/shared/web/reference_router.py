"""Public ``GET /reference/endpoints.json`` — the canonical endpoint reference.

Serves the same payload as the committed ``docs/reference/endpoints.json`` (both
call :func:`jentic_one.shared.web.endpoint_reference.build_reference_payload`), so
the CLI and the docs SPA have one machine-readable source of truth without
parsing vendor extensions out of the OpenAPI document.

Hidden from the OpenAPI schema: it is tooling metadata, not a product API, and
keeping it out of the spec keeps the spec clean.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from jentic_one.shared.web.endpoint_reference import (
    build_reference_payload,
    collect_endpoints,
)

REFERENCE_PATH = "/reference/endpoints.json"

_CACHE_ATTR = "endpoint_reference_payload"


def _cached_payload(app: Any) -> dict[str, Any] | None:
    cached: dict[str, Any] | None = getattr(app.state, _CACHE_ATTR, None)
    return dict(cached) if cached is not None else None


def _reference_payload(
    app: Any,
    source_app: Callable[[], Any] | None,
    build_lock: threading.Lock | None = None,
) -> dict[str, Any]:
    """Build (and cache on app.state) the reference payload for this app.

    The join introspects routes + the curated permission map, which is stable for the
    lifetime of the process, so it is computed once and cached. ``source_app``, when
    given, builds the app to introspect instead of the serving one (a partial
    control-plane process documents the whole control plane, not only its own
    routes). ``build_lock`` single-flights the build: concurrent cold callers wait
    for the first build and reuse its payload instead of each building their own.
    """
    cached = _cached_payload(app)
    if cached is not None:
        return cached
    with build_lock if build_lock is not None else contextlib.nullcontext():
        cached = _cached_payload(app)
        if cached is not None:
            return cached
        endpoints = collect_endpoints(source_app() if source_app is not None else app)
        payload = build_reference_payload(endpoints)
        setattr(app.state, _CACHE_ATTR, payload)
        return payload


def get_reference_router(source_app: Callable[[], Any] | None = None) -> APIRouter:
    """Router exposing the public, schema-hidden endpoint reference.

    By default the reference introspects the serving app. ``source_app`` is a
    zero-argument factory for the app to introspect instead. Building it is
    CPU-bound, so the first (cold) request builds it off the event loop, exactly
    once however many cold requests race; every later request is answered from
    the cached payload on the loop.
    """
    router = APIRouter()
    build_lock = threading.Lock()

    @router.get(REFERENCE_PATH, include_in_schema=False)
    async def endpoints_reference(request: Request) -> JSONResponse:
        if source_app is None:
            return JSONResponse(_reference_payload(request.app, None))
        cached = _cached_payload(request.app)
        if cached is not None:
            return JSONResponse(cached)
        payload = await run_in_threadpool(_reference_payload, request.app, source_app, build_lock)
        return JSONResponse(payload)

    return router
