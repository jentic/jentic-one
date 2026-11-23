"""Self-tests for the callback-trigger router.

Uses an in-process ASGI listener (no real network) injected as the post-back
client, and awaits the scheduled background task deterministically.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from tests.harness.smoke_upstream.routers import callbacks
from tests.harness.smoke_upstream.routers._callbacks_util import set_callback_client

_LISTENER_PATH = "/recv"


@pytest.fixture
async def callback_listener() -> AsyncGenerator[list[dict[str, object]], None]:
    received: list[dict[str, object]] = []
    listener = FastAPI()

    @listener.post(_LISTENER_PATH)
    async def _recv(request: Request) -> dict[str, str]:
        received.append(await request.json())
        return {"status": "ok"}

    transport = ASGITransport(app=listener)
    async with AsyncClient(transport=transport, base_url="http://listener.local") as client:
        set_callback_client(client)
        try:
            yield received
        finally:
            set_callback_client(None)


async def _await_task(app: FastAPI, attr: str) -> None:
    task = getattr(app.state, attr)
    assert isinstance(task, asyncio.Task)
    await task


async def test_trigger_returns_202_and_fires_callback(
    smoke_app: FastAPI,
    smoke_client: AsyncClient,
    callback_listener: list[dict[str, object]],
) -> None:
    response = await smoke_client.post(
        "/callbacks/trigger",
        json={"callback_url": f"http://listener.local{_LISTENER_PATH}"},
    )
    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}

    await _await_task(smoke_app, callbacks.STATE_CALLBACK_TASK)

    assert len(callback_listener) == 1
    assert callback_listener[0]["event"] == callbacks.CALLBACK_EVENT
