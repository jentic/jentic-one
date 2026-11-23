"""Self-tests for the webhook-subscription router.

Uses an in-process ASGI listener injected as the post-back client and awaits the
scheduled background task deterministically.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from tests.harness.smoke_upstream.routers import webhooks
from tests.harness.smoke_upstream.routers._callbacks_util import set_callback_client

_LISTENER_PATH = "/hook"


@pytest.fixture
async def webhook_listener() -> AsyncGenerator[list[dict[str, object]], None]:
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


async def test_subscribe_returns_202_and_fires_webhook(
    smoke_app: FastAPI,
    smoke_client: AsyncClient,
    webhook_listener: list[dict[str, object]],
) -> None:
    response = await smoke_client.post(
        "/webhooks/subscribe",
        json={"callback_url": f"http://listener.local{_LISTENER_PATH}"},
    )
    assert response.status_code == 202
    assert response.json() == {"status": "subscribed"}

    task = getattr(smoke_app.state, webhooks.STATE_WEBHOOK_TASK)
    assert isinstance(task, asyncio.Task)
    await task

    assert len(webhook_listener) == 1
    assert webhook_listener[0]["event"] == webhooks.WEBHOOK_EVENT
