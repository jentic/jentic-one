"""``GET /reference/endpoints.json`` builds its source app once, off the loop (#1445).

A partial control-plane process documents the whole control plane by
introspecting a route-only app that is CPU-bound to build. The endpoint is
public, so a burst of cold requests must not each build their own copy or each
park a threadpool worker, a failed build must not wedge the route, and a warm
request must be answered from the cache without a threadpool hop.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.concurrency import run_in_threadpool

import jentic_one.shared.web.reference_router as reference_router
from jentic_one.shared.web.reference_router import REFERENCE_PATH, get_reference_router


def _source_app() -> FastAPI:
    app = FastAPI()
    router = APIRouter()

    @router.get("/health", operation_id="getHealth", summary="Health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(router)
    return app


@pytest.fixture(autouse=True)
def _stub_join(monkeypatch: pytest.MonkeyPatch) -> None:
    """Swap the route→permission join for a path list: these tests pin caching only.

    The real join refuses a toy app (it requires a populated control plane);
    ``tests/arch/test_endpoint_tree.py`` covers the real payload.
    """

    def _collect(app: FastAPI) -> list[str]:
        return sorted(getattr(route, "path", "") for route in app.routes)

    monkeypatch.setattr(reference_router, "collect_endpoints", _collect)
    monkeypatch.setattr(reference_router, "build_reference_payload", lambda eps: {"paths": eps})


def _count_threadpool_hops(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    hops = [0]

    async def _counting(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        hops[0] += 1
        return await run_in_threadpool(func, *args, **kwargs)

    monkeypatch.setattr(reference_router, "run_in_threadpool", _counting)
    return hops


def _serving_app(source_app: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(get_reference_router(source_app=source_app))
    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def test_concurrent_cold_requests_build_the_source_app_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hops = _count_threadpool_hops(monkeypatch)
    builds = 0
    builds_lock = threading.Lock()

    def _slow_source() -> FastAPI:
        nonlocal builds
        with builds_lock:
            builds += 1
        # Hold the build long enough that every cold request overlaps it.
        time.sleep(0.2)
        return _source_app()

    app = _serving_app(_slow_source)
    async with _client(app) as client:
        responses = await asyncio.gather(*(client.get(REFERENCE_PATH) for _ in range(8)))

    assert [r.status_code for r in responses] == [200] * 8
    assert len({r.content for r in responses}) == 1
    assert builds == 1
    # The waiters wait on the loop: only the one build occupies a worker thread.
    assert hops[0] == 1


async def test_failed_cold_build_is_retried_by_the_next_request() -> None:
    builds = 0

    def _flaky_source() -> FastAPI:
        nonlocal builds
        builds += 1
        if builds == 1:
            raise RuntimeError("source app build failed")
        return _source_app()

    app = _serving_app(_flaky_source)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        failed = await client.get(REFERENCE_PATH)
        recovered = await client.get(REFERENCE_PATH)
        warm = await client.get(REFERENCE_PATH)

    assert failed.status_code == 500
    assert recovered.status_code == 200
    assert warm.json() == recovered.json()
    assert builds == 2


async def test_warm_request_is_served_from_cache_on_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _serving_app(_source_app)
    async with _client(app) as client:
        cold = await client.get(REFERENCE_PATH)

        async def _no_threadpool(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("a warm reference request must not hop to the threadpool")

        monkeypatch.setattr(reference_router, "run_in_threadpool", _no_threadpool)
        warm = await client.get(REFERENCE_PATH)

    assert cold.status_code == 200
    assert warm.status_code == 200
    assert warm.json() == cold.json()
